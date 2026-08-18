"""Phase 1 — Agentic conversational query engine (LangGraph).

Covers: the deterministic tools (aggregate_financials/search_documents/
get_document_status -- numbers only ever come from pal_executor, never the
LLM), the grounding guard (last line of defense against an LLM stating an
unverified number), the full graph loop (tool call -> tool result -> final
answer, with the guard overriding an ungrounded answer), cross-turn memory,
and the LLMUnavailableError propagation when no provider is configured.

All hermetic: the chat model is a scripted fake (LangChain's
FakeMessagesListChatModel with bind_tools overridden to a no-op, since the
base class's bind_tools raises NotImplementedError). No network, no API key,
no real DeepSeek/Gemini/Postgres involved.
"""
from __future__ import annotations

import pandas as pd
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

import agent.graph as agent_graph
import agent.llm as agent_llm
import agent.service as agent_service
from agent.guard import answer_is_grounded, collect_allowed_numbers
from agent.tools import build_tools
from langgraph.checkpoint.memory import MemorySaver
from llm_client import LLMUnavailableError


class ScriptedToolCallingModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel's inherited bind_tools raises NotImplementedError
    (the base class requires provider-specific formatting) -- override it to a
    no-op so create_react_agent-style graphs can bind tools to a scripted fake
    model in tests."""

    def bind_tools(self, tools, **kwargs):
        return self


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "document_id": "IN1", "document_type": "invoice", "date": "2026-07-01",
            "company_name": "AIESEC", "supplier_name": "Virtusa", "order_id": "ORD1",
            "flow_type": "payable", "effective_flow_type": "payable",
            "received_status": "NULL", "paid_status": "not_paid",
            "po_status": "NULL", "dn_status": "NULL", "invoice_status": "pending",
            "due_date": "NULL", "delivery_date": "NULL", "approved_by": "NULL",
            "proof_of_delivery": None, "signed": None,
            "currency": "LKR", "final_total_amount": 5000.0, "payable_amount": 5000.0,
            "raw_total_amount": 5000.0, "items": [],
        },
        {
            "document_id": "IN2", "document_type": "invoice", "date": "2026-06-01",
            "company_name": "AIESEC", "supplier_name": "Classic Printers", "order_id": "ORD2",
            "flow_type": "payable", "effective_flow_type": "payable",
            "received_status": "NULL", "paid_status": "paid",
            "po_status": "NULL", "dn_status": "NULL", "invoice_status": "paid",
            "due_date": "NULL", "delivery_date": "NULL", "approved_by": "NULL",
            "proof_of_delivery": None, "signed": None,
            "currency": "LKR", "final_total_amount": 1500.0, "payable_amount": 1500.0,
            "raw_total_amount": 1500.0, "items": [],
        },
    ])


# ---------------------------------------------------------------------------
# tools.py — numbers only ever come from pal_executor
# ---------------------------------------------------------------------------

def test_aggregate_financials_computes_deterministically(monkeypatch):
    df = _sample_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (df, None))

    tools, evidence = build_tools(user_id="u1", company_name="AIESEC")
    aggregate = next(t for t in tools if t.name == "aggregate_financials")

    result = aggregate.invoke({"measure_field": "total", "agg": "sum", "filters": []})

    assert result["value"] == 6500.0
    assert result["currency"] == "LKR"
    assert result["row_count"] == 2
    assert len(evidence) == 2
    assert {e["document_id"] for e in evidence} == {"IN1", "IN2"}


def test_aggregate_financials_applies_filters(monkeypatch):
    df = _sample_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (df, None))
    tools, _ = build_tools(user_id="u1", company_name="AIESEC")
    aggregate = next(t for t in tools if t.name == "aggregate_financials")

    result = aggregate.invoke({
        "measure_field": "total", "agg": "sum",
        "filters": [{"field": "vendor", "op": "contains", "value": "Virtusa"}],
    })

    assert result["value"] == 5000.0
    assert result["row_count"] == 1


def test_aggregate_financials_outstanding_payable_excludes_paid(monkeypatch):
    """IN1 is not_paid (5000.0), IN2 is paid (1500.0) -- an 'outstanding
    payable' style call must exclude IN2, matching the tool docstring's
    guidance for 'how much do we still owe'."""
    df = _sample_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (df, None))
    tools, _ = build_tools(user_id="u1", company_name="AIESEC")
    aggregate = next(t for t in tools if t.name == "aggregate_financials")

    result = aggregate.invoke({
        "measure_field": "total", "agg": "sum",
        "filters": [
            {"field": "flow_type", "op": "eq", "value": "payable"},
            {"field": "paid_status", "op": "in", "value": ["not_paid", "partial"]},
        ],
    })

    assert result["value"] == 5000.0
    assert result["row_count"] == 1


def test_aggregate_financials_rejects_non_canonical_field(monkeypatch):
    df = _sample_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (df, None))
    tools, _ = build_tools(user_id="u1", company_name="AIESEC")
    aggregate = next(t for t in tools if t.name == "aggregate_financials")

    result = aggregate.invoke({"measure_field": "secret_internal_id", "agg": "sum"})

    assert "error" in result
    assert "field_not_canonical" in result["error"]


def test_aggregate_financials_no_documents_returns_error(monkeypatch):
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4",
                        lambda company, user: (pd.DataFrame(), "No records found."))
    tools, _ = build_tools(user_id="u1", company_name="Nobody")
    aggregate = next(t for t in tools if t.name == "aggregate_financials")

    result = aggregate.invoke({"measure_field": "total", "agg": "sum"})

    assert result == {"error": "No records found."}


def test_search_documents_filters_by_type(monkeypatch):
    df = _sample_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_rag", lambda q, company, user: (df, None))
    tools, evidence = build_tools(user_id="u1", company_name="AIESEC")
    search = next(t for t in tools if t.name == "search_documents")

    result = search.invoke({"query": "invoices", "document_type": "invoice", "limit": 10})

    assert result["count"] == 2
    assert len(evidence) == 2


def test_search_documents_no_match_returns_empty(monkeypatch):
    df = _sample_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_rag", lambda q, company, user: (df, None))
    tools, _ = build_tools(user_id="u1", company_name="AIESEC")
    search = next(t for t in tools if t.name == "search_documents")

    result = search.invoke({"query": "", "document_type": "po", "limit": 10})

    assert result == {"documents": [], "count": 0, "note": "No po documents found."}


def test_get_document_status_looks_up_one_document(monkeypatch):
    df = _sample_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (df, None))
    tools, evidence = build_tools(user_id="u1", company_name="AIESEC")
    lookup = next(t for t in tools if t.name == "get_document_status")

    result = lookup.invoke({"document_id": "in1"})  # case-insensitive

    assert result["document_id"] == "IN1"
    assert result["invoice_status"] == "pending"
    assert len(evidence) == 1


def test_get_document_status_not_found(monkeypatch):
    df = _sample_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (df, None))
    tools, _ = build_tools(user_id="u1", company_name="AIESEC")
    lookup = next(t for t in tools if t.name == "get_document_status")

    result = lookup.invoke({"document_id": "IN999"})

    assert "error" in result


# ---------------------------------------------------------------------------
# guard.py — last line of defense against an ungrounded answer
# ---------------------------------------------------------------------------

def test_collect_allowed_numbers_from_dict_tool_output():
    allowed = collect_allowed_numbers([{"value": 6500.0, "row_count": 2}])
    assert 6500.0 in allowed
    assert 2.0 in allowed


def test_collect_allowed_numbers_from_json_string_tool_output():
    allowed = collect_allowed_numbers(['{"value": 1500.0}'])
    assert 1500.0 in allowed


def test_answer_is_grounded_true_when_number_matches():
    assert answer_is_grounded("Your total is LKR 6,500.00.", {6500.0}) is True


def test_answer_is_grounded_false_when_number_invented():
    assert answer_is_grounded("Your total is LKR 999,999.00.", {6500.0}) is False


def test_answer_is_grounded_ignores_years():
    assert answer_is_grounded("As of 2026, you have no pending items.", set()) is True


# ---------------------------------------------------------------------------
# graph.py — full loop, hermetic (scripted fake model)
# ---------------------------------------------------------------------------

def _patch_agent_llm(monkeypatch, model):
    monkeypatch.setattr(agent_llm, "get_chat_model", lambda temperature=0.0: model)
    monkeypatch.setattr(agent_graph, "get_chat_model", lambda temperature=0.0: model)


def test_graph_calls_tool_and_returns_grounded_answer(monkeypatch):
    df = _sample_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (df, None))

    responses = [
        AIMessage(content="", tool_calls=[{
            "name": "aggregate_financials",
            "args": {"measure_field": "total", "agg": "sum", "filters": []},
            "id": "call_1",
        }]),
        AIMessage(content="For AIESEC, the total payable is LKR 6,500.00."),
    ]
    model = ScriptedToolCallingModel(responses=responses)
    _patch_agent_llm(monkeypatch, model)

    tools, evidence = build_tools(user_id="u1", company_name="AIESEC")
    graph = agent_graph.build_agent_graph(tools, MemorySaver(), company_name="AIESEC")

    result = graph.invoke(
        {"messages": [HumanMessage(content="how much do we owe?")]},
        config={"configurable": {"thread_id": "t1"}},
    )

    final = result["messages"][-1]
    assert isinstance(final, AIMessage)
    assert "6,500.00" in final.content
    assert len(evidence) == 2


def test_graph_guard_overrides_ungrounded_final_answer(monkeypatch):
    df = _sample_df()
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (df, None))

    # Tool truthfully computes 6500.0, but the scripted "LLM" then states a
    # different, invented figure -- the guard must catch and override this.
    responses = [
        AIMessage(content="", tool_calls=[{
            "name": "aggregate_financials",
            "args": {"measure_field": "total", "agg": "sum", "filters": []},
            "id": "call_1",
        }]),
        AIMessage(content="For AIESEC, the total payable is LKR 999,999.00."),
    ]
    model = ScriptedToolCallingModel(responses=responses)
    _patch_agent_llm(monkeypatch, model)

    tools, _ = build_tools(user_id="u1", company_name="AIESEC")
    graph = agent_graph.build_agent_graph(tools, MemorySaver(), company_name="AIESEC")

    result = graph.invoke(
        {"messages": [HumanMessage(content="how much do we owe?")]},
        config={"configurable": {"thread_id": "t1"}},
    )

    final = result["messages"][-1]
    # The invented figure is gone, and the guard replaces the answer with a clean
    # message — NOT a raw dump of the tool JSON (that belongs in the derivation
    # trace / Sources panel, not the chat bubble).
    assert "999,999" not in final.content
    assert "verify" in final.content.lower()
    assert "{" not in final.content and "6500" not in final.content


def test_graph_greeting_needs_no_tool_call(monkeypatch):
    responses = [AIMessage(content="Hello! How can I help with AIESEC's finances today?")]
    model = ScriptedToolCallingModel(responses=responses)
    _patch_agent_llm(monkeypatch, model)

    tools, evidence = build_tools(user_id="u1", company_name="AIESEC")
    graph = agent_graph.build_agent_graph(tools, MemorySaver(), company_name="AIESEC")

    result = graph.invoke(
        {"messages": [HumanMessage(content="hello")]},
        config={"configurable": {"thread_id": "t1"}},
    )

    final = result["messages"][-1]
    assert "Hello" in final.content
    assert evidence == []  # no tool was called -- no irrelevant discrepancy-style bolt-ons


def test_graph_memory_persists_across_turns(monkeypatch):
    responses = [
        AIMessage(content="Hello! How can I help with AIESEC's finances today?"),
        AIMessage(content="You previously said hello."),
    ]
    model = ScriptedToolCallingModel(responses=responses)
    _patch_agent_llm(monkeypatch, model)

    tools, _ = build_tools(user_id="u1", company_name="AIESEC")
    checkpointer = MemorySaver()
    graph = agent_graph.build_agent_graph(tools, checkpointer, company_name="AIESEC")
    config = {"configurable": {"thread_id": "t1"}}

    graph.invoke({"messages": [HumanMessage(content="hello")]}, config=config)
    result2 = graph.invoke({"messages": [HumanMessage(content="what did I say?")]}, config=config)

    # 2 messages from turn 1 + 2 from turn 2 = 4 accumulated in the thread.
    assert len(result2["messages"]) == 4


# ---------------------------------------------------------------------------
# service.py — LLMUnavailableError propagation (no offline fallback)
# ---------------------------------------------------------------------------

def test_chat_raises_when_no_llm_provider_configured(monkeypatch):
    def _boom(temperature=0.0):
        raise LLMUnavailableError("no provider configured")

    monkeypatch.setattr(agent_llm, "get_chat_model", _boom)
    monkeypatch.setattr(agent_graph, "get_chat_model", _boom)
    monkeypatch.setattr("agent.tools.resolve_scope_with_c4", lambda company, user: (_sample_df(), None))

    with pytest.raises(LLMUnavailableError):
        agent_service.chat(question="hi", user_id="u1", company_name="AIESEC")
