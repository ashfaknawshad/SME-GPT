"""Phase 3 Stage A — chat thread registry, derivation trace, discrepancy tool.

Covers: agent/threads.py CRUD (tenant-scoped, against a fake DB connection --
same hermetic pattern as _log_audit_event's tests), service.extract_turn_trace
(pure function), the find_discrepancies tool (only comparing invoice-vs-PO
within the SAME order), service.chat returning + persisting the trace, and the
/chat/threads endpoints via TestClient (registry monkeypatched).

All hermetic: no network, no API key, no real Postgres.
"""
from __future__ import annotations

import os

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agent.threads as threads_mod
from agent.service import extract_turn_trace
from agent.tools import build_tools

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))
except Exception:
    pass


# ---------------------------------------------------------------------------
# Fake DB plumbing (context-manager get_conn -> conn.cursor() -> execute/fetch)
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, rows=None, rowcount=1):
        self.executed: list[tuple[str, tuple]] = []
        self._rows = rows or []
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _fake_get_conn(cursor):
    from contextlib import contextmanager

    @contextmanager
    def get_conn():
        yield FakeConn(cursor)

    return get_conn


# ---------------------------------------------------------------------------
# threads.record_turn
# ---------------------------------------------------------------------------

def test_record_turn_upserts_thread_and_appends_both_messages(monkeypatch):
    cur = FakeCursor()
    monkeypatch.setattr(threads_mod, "get_conn", _fake_get_conn(cur))

    threads_mod.record_turn(
        thread_id="u1:t1", user_id="u1", company_name="AIESEC",
        question="how much do we owe?", answer="LKR 700.00 outstanding.",
        evidence=[{"document_id": "PO1"}], trace=[{"step": 1, "tool": "aggregate_financials"}],
    )

    sqls = [sql for sql, _ in cur.executed]
    assert len(sqls) == 3
    assert "INSERT INTO chat_thread" in sqls[0] and "ON CONFLICT (id) DO UPDATE" in sqls[0]
    assert "INSERT INTO chat_message" in sqls[1] and "'user'" in sqls[1]
    assert "INSERT INTO chat_message" in sqls[2] and "'assistant'" in sqls[2]
    # tenant id travels with every row
    for _, params in cur.executed:
        assert "u1" in params


def test_record_turn_swallows_db_failure(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(threads_mod, "get_conn", _boom)
    # Must not raise -- a registry failure never fails the chat request.
    threads_mod.record_turn("u1:t1", "u1", "AIESEC", "q", "a")


def test_generate_thread_title_cleans_llm_output(monkeypatch):
    import agent.service as agent_service
    import llm_client
    monkeypatch.setattr(llm_client, "call_llm", lambda *a, **k: '  "Outstanding payables check"  \n')
    title = agent_service.generate_thread_title("how much do we owe?", "LKR 700 outstanding.")
    assert title == "Outstanding payables check"


def test_generate_thread_title_returns_none_on_failure(monkeypatch):
    import agent.service as agent_service
    import llm_client
    monkeypatch.setattr(llm_client, "call_llm",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no provider")))
    assert agent_service.generate_thread_title("q", "a") is None


def test_record_turn_title_is_shortened(monkeypatch):
    cur = FakeCursor()
    monkeypatch.setattr(threads_mod, "get_conn", _fake_get_conn(cur))
    long_q = "x" * 500

    threads_mod.record_turn("u1:t1", "u1", "AIESEC", long_q, "a")

    _, params = cur.executed[0]
    title = params[3]
    assert len(title) <= threads_mod._TITLE_MAX


# ---------------------------------------------------------------------------
# threads list/get/rename/delete -- tenant scoping
# ---------------------------------------------------------------------------

def test_list_threads_filters_by_user(monkeypatch):
    cur = FakeCursor(rows=[{
        "id": "u1:t1", "company_name": "AIESEC", "title": "how much do we owe?",
        "last_message_preview": "LKR 700.00", "document_id": None,
        "created_at": "2026-07-16", "updated_at": "2026-07-16",
    }])
    monkeypatch.setattr(threads_mod, "get_conn", _fake_get_conn(cur))

    result = threads_mod.list_threads("u1")

    assert result[0]["thread_id"] == "u1:t1"
    sql, params = cur.executed[0]
    assert "WHERE user_id = %s" in sql
    assert params == ("u1",)


def test_get_thread_messages_returns_none_for_foreign_thread(monkeypatch):
    cur = FakeCursor(rows=[])  # ownership check finds nothing
    monkeypatch.setattr(threads_mod, "get_conn", _fake_get_conn(cur))

    assert threads_mod.get_thread_messages("other-user:t9", "u1") is None


def test_rename_thread_returns_false_when_not_owned(monkeypatch):
    cur = FakeCursor(rowcount=0)
    monkeypatch.setattr(threads_mod, "get_conn", _fake_get_conn(cur))

    assert threads_mod.rename_thread("other:t9", "u1", "new title") is False


def test_delete_thread_clears_checkpointer_when_deleted(monkeypatch):
    cur = FakeCursor(rowcount=1)
    monkeypatch.setattr(threads_mod, "get_conn", _fake_get_conn(cur))

    cleared: dict = {}

    class FakeCheckpointer:
        def delete_thread(self, thread_id):
            cleared["thread_id"] = thread_id

    import agent.memory as memory_mod
    monkeypatch.setattr(memory_mod, "get_checkpointer", lambda: FakeCheckpointer())

    assert threads_mod.delete_thread("u1:t1", "u1") is True
    assert cleared["thread_id"] == "u1:t1"


# ---------------------------------------------------------------------------
# service.extract_turn_trace
# ---------------------------------------------------------------------------

def test_extract_turn_trace_pairs_calls_with_results():
    messages = [
        HumanMessage(content="earlier turn"),
        AIMessage(content="earlier answer"),
        HumanMessage(content="how much do we owe?"),
        AIMessage(content="", tool_calls=[{
            "name": "aggregate_financials",
            "args": {"measure_field": "total", "agg": "sum"},
            "id": "call_1",
        }]),
        ToolMessage(content='{"value": 700.0, "row_count": 2}', tool_call_id="call_1"),
        AIMessage(content="LKR 700.00 outstanding."),
    ]
    trace = extract_turn_trace(messages)

    assert len(trace) == 1
    assert trace[0]["tool"] == "aggregate_financials"
    assert trace[0]["args"]["agg"] == "sum"
    assert "700.0" in trace[0]["result"]


def test_extract_turn_trace_only_covers_current_turn():
    messages = [
        HumanMessage(content="turn 1"),
        AIMessage(content="", tool_calls=[{"name": "search_documents", "args": {}, "id": "old"}]),
        ToolMessage(content="{}", tool_call_id="old"),
        AIMessage(content="turn 1 answer"),
        HumanMessage(content="turn 2 -- no tools needed"),
        AIMessage(content="hello!"),
    ]
    assert extract_turn_trace(messages) == []


def test_extract_turn_trace_truncates_huge_results():
    messages = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]),
        ToolMessage(content="x" * 5000, tool_call_id="c1"),
        AIMessage(content="a"),
    ]
    trace = extract_turn_trace(messages)
    assert len(trace[0]["result"]) < 1000


# ---------------------------------------------------------------------------
# find_discrepancies tool
# ---------------------------------------------------------------------------

def _doc_row(doc_id, doc_type, order_id, items):
    return {
        "document_id": doc_id, "document_type": doc_type, "date": "2026-04-16",
        "company_name": "AIESEC", "supplier_name": "Colombo Mart", "order_id": order_id,
        "flow_type": "payable", "effective_flow_type": "payable",
        "received_status": "NULL", "paid_status": "not_paid",
        "po_status": "NULL", "dn_status": "NULL", "invoice_status": "NULL",
        "due_date": "NULL", "delivery_date": "NULL", "approved_by": "NULL",
        "proof_of_delivery": None, "signed": None,
        "currency": "LKR", "final_total_amount": 1000.0, "payable_amount": 1000.0,
        "raw_total_amount": 1000.0, "items": items,
    }


def _discrepancy_df():
    return pd.DataFrame([
        _doc_row("IN1", "invoice", "PO-2026-501",
                 [{"description": "Sugar bags 25kg", "quantity": 20, "unit_price": 5400.0, "line_total": 108000.0}]),
        _doc_row("PO4", "po", "PO-2026-501",
                 [{"description": "Sugar bags 25kg", "quantity": 20, "unit_price": 4250.0, "line_total": 85000.0}]),
        # unrelated order with only an invoice -- must be skipped, not compared cross-order
        _doc_row("IN2", "invoice", "PO-2026-999",
                 [{"description": "Tea packets", "quantity": 5, "unit_price": 850.0, "line_total": 4250.0}]),
    ])


def test_find_discrepancies_detects_invoice_vs_po_gap(monkeypatch):
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (_discrepancy_df(), None))
    tools, evidence = build_tools(user_id="u1", company_name="AIESEC")
    find = next(t for t in tools if t.name == "find_discrepancies")

    result = find.invoke({})

    assert result["orders_checked"] == 1  # only PO-2026-501 has both docs
    real = [d for d in result["discrepancies"] if d["is_discrepancy"]]
    assert len(real) == 1
    assert real[0]["invoice_price"] == 5400.0
    assert real[0]["po_price"] == 4250.0
    assert real[0]["order_id"] == "PO-2026-501"
    assert len(evidence) == 2  # the discrepant order's invoice + PO


def test_find_discrepancies_scoped_to_order_id(monkeypatch):
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (_discrepancy_df(), None))
    tools, _ = build_tools(user_id="u1", company_name="AIESEC")
    find = next(t for t in tools if t.name == "find_discrepancies")

    result = find.invoke({"order_id": "PO-2026-999"})

    # that order has no PO counterpart -> nothing to compare
    assert result["orders_checked"] == 0
    assert result["discrepancies"] == []


def test_find_discrepancies_unknown_order_returns_error(monkeypatch):
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (_discrepancy_df(), None))
    tools, _ = build_tools(user_id="u1", company_name="AIESEC")
    find = next(t for t in tools if t.name == "find_discrepancies")

    result = find.invoke({"order_id": "NO-SUCH-ORDER"})

    assert "error" in result


# ---------------------------------------------------------------------------
# service.chat returns trace + persists the turn
# ---------------------------------------------------------------------------

def test_chat_returns_trace_and_records_turn(monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    class ScriptedToolCallingModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    df = _discrepancy_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (df, None))

    responses = [
        AIMessage(content="", tool_calls=[{
            "name": "aggregate_financials",
            "args": {"measure_field": "total", "agg": "sum", "filters": []},
            "id": "call_1",
        }]),
        AIMessage(content="The total is LKR 3,000.00."),
    ]
    model = ScriptedToolCallingModel(responses=responses)

    import agent.graph as agent_graph
    import agent.service as agent_service
    from langgraph.checkpoint.memory import MemorySaver

    monkeypatch.setattr(agent_graph, "get_chat_model", lambda temperature=0.0: model)
    monkeypatch.setattr(agent_service, "get_checkpointer", lambda: MemorySaver())
    monkeypatch.setattr(agent_service, "generate_thread_title", lambda q, a: None)

    recorded: dict = {}

    def fake_record_turn(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(agent_service, "record_turn", fake_record_turn)

    result = agent_service.chat(question="total?", user_id="u1", company_name="AIESEC")

    assert result["trace"], "trace missing from chat response"
    assert result["trace"][0]["tool"] == "aggregate_financials"
    assert recorded["thread_id"] == result["thread_id"]
    assert recorded["question"] == "total?"
    assert recorded["trace"] == result["trace"]
    assert recorded["evidence"] == result["evidence"]


# ---------------------------------------------------------------------------
# /chat/threads endpoints (TestClient; registry monkeypatched)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app_module():
    import app as app_mod
    return app_mod


@pytest.fixture()
def client(app_module):
    return TestClient(app_module.app, raise_server_exceptions=False)


def _bearer(app_module, **claims):
    import jwt as pyjwt
    return "Bearer " + pyjwt.encode(claims, app_module.JWT_SECRET, algorithm=app_module.JWT_ALGORITHM)


def test_threads_endpoints_disabled_without_flag(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "AGENT_QUERY_ENGINE_ENABLED", False)
    resp = client.get("/chat/threads",
                      headers={"Authorization": _bearer(app_module, userId="u1", role="owner")})
    assert resp.status_code == 503


def test_list_threads_endpoint(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "AGENT_QUERY_ENGINE_ENABLED", True)
    monkeypatch.setattr(threads_mod, "list_threads",
                        lambda user_id: [{"thread_id": f"{user_id}:t1", "title": "T"}])
    resp = client.get("/chat/threads",
                      headers={"Authorization": _bearer(app_module, userId="u1", role="owner")})
    assert resp.status_code == 200
    assert resp.json()["threads"][0]["thread_id"] == "u1:t1"


def test_get_thread_endpoint_404_when_missing(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "AGENT_QUERY_ENGINE_ENABLED", True)
    monkeypatch.setattr(threads_mod, "get_thread_messages", lambda tid, uid: None)
    resp = client.get("/chat/threads/nope",
                      headers={"Authorization": _bearer(app_module, userId="u1", role="owner")})
    assert resp.status_code == 404


def test_rename_thread_endpoint(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "AGENT_QUERY_ENGINE_ENABLED", True)
    monkeypatch.setattr(threads_mod, "rename_thread", lambda tid, uid, title: True)
    resp = client.patch("/chat/threads/u1:t1", json={"title": "My renamed topic"},
                        headers={"Authorization": _bearer(app_module, userId="u1", role="owner")})
    assert resp.status_code == 200
    assert resp.json()["title"] == "My renamed topic"


def test_delete_thread_endpoint_404_when_not_owned(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "AGENT_QUERY_ENGINE_ENABLED", True)
    monkeypatch.setattr(threads_mod, "delete_thread", lambda tid, uid: False)
    resp = client.delete("/chat/threads/other:t9",
                         headers={"Authorization": _bearer(app_module, userId="u1", role="owner")})
    assert resp.status_code == 404
