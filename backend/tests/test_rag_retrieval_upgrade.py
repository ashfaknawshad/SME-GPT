"""RAG retrieval-quality layers: RRF fusion, bilingual query expansion,
reranker pass-through, and search_documents chunk-provenance surfacing.

Hermetic: the pure functions (rrf_merge, _expand_query_for_lexical) and the
flag-off reranker need no DB/model; the DB arms are exercised via the existing
DATABASE_URL-gated integration path elsewhere.
"""
from __future__ import annotations

import pandas as pd

import rerank_service
import vector_index
from vector_index import rrf_merge, _expand_query_for_lexical, _or_tsquery


# ── OR tsquery builder (recall arm) ───────────────────────────────────────────

def test_or_tsquery_joins_tokens_with_or():
    assert _or_tsquery("terms and conditions") == "terms | and | conditions"


def test_or_tsquery_strips_punctuation_and_is_injection_safe():
    # only \w+ tokens survive -> no way to inject tsquery operators
    assert _or_tsquery("total: 5,000 & (drop)") == "total | 5 | 000 | drop"


def test_or_tsquery_handles_sinhala_and_empty():
    assert _or_tsquery("රැවුල කැපිමට") == "රැවුල | කැපිමට"
    assert _or_tsquery("   ") == ""


# ── RRF fusion (pure) ─────────────────────────────────────────────────────────

def test_rrf_merge_rewards_agreement_across_lists():
    dense = [{"chunk_id": "a"}, {"chunk_id": "b"}, {"chunk_id": "c"}]
    lexical = [{"chunk_id": "b"}, {"chunk_id": "d"}, {"chunk_id": "a"}]
    fused = rrf_merge([dense, lexical], k=4)
    ids = [f["chunk_id"] for f in fused]
    # 'b' (rank0 lexical + rank1 dense) and 'a' (rank0 dense + rank2 lexical)
    # should outrank items appearing in only one list.
    assert ids[0] in ("a", "b")
    assert set(ids[:2]) == {"a", "b"}
    assert "fusion_score" in fused[0]


def test_rrf_merge_dedupes_and_truncates():
    a = [{"chunk_id": "x"}, {"chunk_id": "y"}]
    b = [{"chunk_id": "x"}, {"chunk_id": "z"}]
    fused = rrf_merge([a, b], k=2)
    assert len(fused) == 2
    assert len({f["chunk_id"] for f in fused}) == 2  # no duplicate x


def test_rrf_merge_keeps_richest_fields():
    dense = [{"chunk_id": "a", "text": "hello", "page": 1}]
    lexical = [{"chunk_id": "a", "lex_rank": 0.9}]
    fused = rrf_merge([dense, lexical], k=1)
    assert fused[0]["text"] == "hello"
    assert fused[0]["page"] == 1
    assert "lex_rank" in fused[0]


def test_rrf_merge_ignores_items_without_key():
    fused = rrf_merge([[{"nope": 1}, {"chunk_id": "a"}]], k=5)
    assert [f["chunk_id"] for f in fused] == ["a"]


# ── bilingual query expansion (lexical arm only) ──────────────────────────────

def test_expand_query_appends_normalized_when_different(monkeypatch):
    import data_tools
    monkeypatch.setattr(data_tools, "normalize_query", lambda q: "payable amount")
    out = _expand_query_for_lexical("ගෙවිය යුතු")
    assert "ගෙවිය යුතු" in out and "payable amount" in out


def test_expand_query_unchanged_when_normalized_equal(monkeypatch):
    import data_tools
    monkeypatch.setattr(data_tools, "normalize_query", lambda q: q)
    assert _expand_query_for_lexical("total sales") == "total sales"


def test_expand_query_falls_back_on_error(monkeypatch):
    import data_tools
    monkeypatch.setattr(data_tools, "normalize_query",
                        lambda q: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _expand_query_for_lexical("x") == "x"


# ── reranker: off by default (pass-through), safe on failure ──────────────────

def test_rerank_passthrough_when_disabled(monkeypatch):
    monkeypatch.setattr(rerank_service, "rerank_enabled", lambda: False)
    cands = [{"chunk_id": "a"}, {"chunk_id": "b"}, {"chunk_id": "c"}]
    assert rerank_service.rerank("q", cands, top_k=2) == cands[:2]


def test_rerank_swallows_model_failure(monkeypatch):
    monkeypatch.setattr(rerank_service, "rerank_enabled", lambda: True)
    monkeypatch.setattr(rerank_service, "_load",
                        lambda: (_ for _ in ()).throw(RuntimeError("no model")))
    cands = [{"chunk_id": "a"}, {"chunk_id": "b"}]
    assert rerank_service.rerank("q", cands, top_k=1) == cands[:1]


def test_rerank_reorders_by_cross_encoder_score(monkeypatch):
    monkeypatch.setattr(rerank_service, "rerank_enabled", lambda: True)

    class FakeCE:
        def predict(self, pairs):
            # score = length of the candidate text (so "bbb" > "a")
            return [len(text) for _, text in pairs]

    monkeypatch.setattr(rerank_service, "_load", lambda: FakeCE())
    cands = [{"chunk_id": "a", "text": "a"}, {"chunk_id": "b", "text": "bbb"}]
    out = rerank_service.rerank("q", cands, top_k=2)
    assert [c["chunk_id"] for c in out] == ["b", "a"]
    assert "rerank_score" in out[0]


# ── search_documents surfaces chunk provenance ────────────────────────────────

def test_search_documents_attaches_matched_snippets(monkeypatch):
    from agent.tools import build_tools

    df = pd.DataFrame([{
        "document_id": "IN1", "document_type": "invoice", "date": "2026-07-01",
        "company_name": "AIESEC", "supplier_name": "Virtusa", "order_id": "O1",
        "flow_type": "payable", "effective_flow_type": "payable",
        "received_status": "NULL", "paid_status": "not_paid",
        "po_status": "NULL", "dn_status": "NULL", "invoice_status": "pending",
        "due_date": "NULL", "delivery_date": "NULL", "approved_by": "NULL",
        "proof_of_delivery": None, "signed": None,
        "currency": "LKR", "final_total_amount": 5000.0, "payable_amount": 5000.0,
        "raw_total_amount": 5000.0, "items": [],
    }])
    monkeypatch.setattr("agent.tools.resolve_scope_with_rag", lambda q, company, user, **kw: (df, None))
    monkeypatch.setattr("agent.tools.retrieve_rag_chunks", lambda q, user, k=6: [
        {"document_id": "IN1", "chunk_type": "line_item_row", "page": 1,
         "bbox": [0, 0, 1, 1], "text": "LineItem | Description: Sugar | Total: 5000"},
        {"document_id": "OTHER", "chunk_type": "section_text", "page": 1,
         "bbox": [0, 0, 1, 1], "text": "unrelated"},
    ])

    tools, _ = build_tools(user_id="u1", company_name="AIESEC")
    search = next(t for t in tools if t.name == "search_documents")
    result = search.invoke({"query": "sugar invoice", "limit": 10})

    assert result["count"] == 1
    assert "matched_snippets" in result
    # only snippets from matched docs (IN1), not OTHER
    assert [s["document_id"] for s in result["matched_snippets"]] == ["IN1"]
    assert result["matched_snippets"][0]["chunk_type"] == "line_item_row"
