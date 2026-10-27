"""Iteration 4 — vector indexing & retrieval tests.

Covers: the deterministic `HashingEmbeddingService` (no model/network needed,
keeps the suite hermetic), chunk-flattening from `build_spatial_chunks()`
output, in-memory cosine-similarity ranking (`rank_embedded_rows`), and a
retrieval-hit-rate harness on a small labelled query set against the same
mock fixture C2's tests use. The pgvector-backed `upsert_chunk_embeddings`/
`retrieve_top_k` round-trip is a DB integration test, skipped when
DATABASE_URL is unset (same pattern as `tests/test_iter1_data_layer.py`).
"""
import os

import pytest

from embedding_service import (
    EMBEDDING_DIM,
    ColabEmbeddingService,
    HashingEmbeddingService,
    get_embedding_service,
)
from ocr_service import get_ocr_service
from spatial_serialization import build_spatial_chunks
from vector_index import embed_rows, flatten_chunks_for_embedding, rank_embedded_rows


def _mock_spatial_chunks():
    service = get_ocr_service()
    pages = service.run(["invoice.png"])
    final_safe_boxes = [{"page": i + 1, "boxes": boxes} for i, boxes in enumerate(pages)]
    return build_spatial_chunks(final_safe_boxes, tenant_id="t1", document_id="d1")


# ---------------------------------------------------------------------------
# HashingEmbeddingService
# ---------------------------------------------------------------------------

def test_hashing_embedding_is_deterministic():
    service = HashingEmbeddingService()
    a = service.embed(["Order ID: 8"])[0]
    b = service.embed(["Order ID: 8"])[0]
    assert a == b


def test_hashing_embedding_is_unit_normalized():
    service = HashingEmbeddingService()
    vec = service.embed(["Total Rs 1,300"])[0]
    norm = sum(v * v for v in vec) ** 0.5
    assert abs(norm - 1.0) < 1e-9


def test_hashing_embedding_empty_text_is_zero_vector():
    service = HashingEmbeddingService()
    vec = service.embed([""])[0]
    assert all(v == 0.0 for v in vec)


def test_get_embedding_service_hashing_engine():
    service = get_embedding_service("hashing")
    assert isinstance(service, HashingEmbeddingService)


def test_get_embedding_service_unknown_engine_raises():
    with pytest.raises(NotImplementedError):
        get_embedding_service("openai")


# ---------------------------------------------------------------------------
# ColabEmbeddingService (remote /embed, mocked -- no network)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_colab_embedding_posts_raw_texts_in_batches(monkeypatch):
    import requests

    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _FakeResponse({"embeddings": [[0.0] * EMBEDDING_DIM for _ in json["texts"]]})

    monkeypatch.setattr(requests, "post", fake_post)
    service = ColabEmbeddingService(base_url="https://x.ngrok.app/")
    vectors = service.embed([f"t{i}" for i in range(ColabEmbeddingService.BATCH_SIZE + 1)])

    assert len(vectors) == ColabEmbeddingService.BATCH_SIZE + 1
    assert [len(c[1]["texts"]) for c in calls] == [ColabEmbeddingService.BATCH_SIZE, 1]
    assert calls[0][0] == "https://x.ngrok.app/embed"
    # The notebook adds the e5 prefix itself -- the client must not double it.
    assert calls[0][1]["texts"][0] == "t0"
    assert calls[0][1]["mode"] == "passage"


def test_colab_embedding_query_uses_short_timeout(monkeypatch):
    import requests

    seen = {}

    def fake_post(url, json, timeout):
        seen["timeout"] = timeout
        return _FakeResponse({"embeddings": [[0.0] * EMBEDDING_DIM]})

    monkeypatch.setattr(requests, "post", fake_post)
    service = ColabEmbeddingService(base_url="https://x.ngrok.app")
    service.embed(["how much do we owe?"], mode="query")
    assert seen["timeout"] == service.query_timeout < service.passage_timeout


def test_colab_embedding_rejects_wrong_dimension(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "post", lambda url, json, timeout: _FakeResponse({"embeddings": [[0.0] * 768]}))
    with pytest.raises(RuntimeError):
        ColabEmbeddingService(base_url="https://x.ngrok.app").embed(["a"])


def test_colab_embedding_without_url_raises(monkeypatch):
    monkeypatch.delenv("COLAB_OCR_URL", raising=False)
    with pytest.raises(RuntimeError):
        ColabEmbeddingService().embed(["a"])


def test_get_embedding_service_reads_engine_from_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_ENGINE", "colab")
    assert isinstance(get_embedding_service(), ColabEmbeddingService)


# ---------------------------------------------------------------------------
# flatten_chunks_for_embedding
# ---------------------------------------------------------------------------

def test_flatten_chunks_for_embedding_preserves_provenance():
    spatial = _mock_spatial_chunks()
    rows = flatten_chunks_for_embedding(spatial)

    total_chunks = sum(len(p["chunks"]) for p in spatial["pages"])
    assert len(rows) == total_chunks
    for row in rows:
        assert row["tenant_id"] == "t1"
        assert row["document_id"] == "d1"
        assert isinstance(row["page"], int)
        assert isinstance(row["bbox"], list) and len(row["bbox"]) == 4
        assert row["text"]


# ---------------------------------------------------------------------------
# rank_embedded_rows
# ---------------------------------------------------------------------------

def test_rank_embedded_rows_orders_by_similarity_descending():
    service = HashingEmbeddingService()
    rows = embed_rows(
        [
            {"chunk_id": "a", "text": "Apple Banana Cherry"},
            {"chunk_id": "b", "text": "Apple Banana"},
            {"chunk_id": "c", "text": "Completely unrelated text"},
        ],
        service=service,
    )
    query_vector = service.embed(["Apple Banana Cherry"], mode="query")[0]
    ranked = rank_embedded_rows(query_vector, rows, k=3)

    assert [r["chunk_id"] for r in ranked] == ["a", "b", "c"]
    assert ranked[0]["similarity"] >= ranked[1]["similarity"] >= ranked[2]["similarity"]


def test_rank_embedded_rows_respects_k():
    service = HashingEmbeddingService()
    rows = embed_rows([{"chunk_id": str(i), "text": f"item {i}"} for i in range(10)], service=service)
    query_vector = service.embed(["item 5"], mode="query")[0]
    ranked = rank_embedded_rows(query_vector, rows, k=3)
    assert len(ranked) == 3


def test_embed_rows_drops_embedding_key_from_ranked_output():
    service = HashingEmbeddingService()
    rows = embed_rows([{"chunk_id": "a", "text": "hello"}], service=service)
    assert "embedding" in rows[0]
    ranked = rank_embedded_rows(service.embed(["hello"], mode="query")[0], rows, k=1)
    assert "embedding" not in ranked[0]
    assert "similarity" in ranked[0]


# ---------------------------------------------------------------------------
# Retrieval hit-rate harness (ROADMAP Iteration 4: "retrieval hit-rate harness
# on labelled queries"), against the same mock fixture C2's tests use.
# ---------------------------------------------------------------------------

# (query, substring expected in the top-ranked chunk's text)
_LABELLED_QUERIES = [
    ("රැවුල කැපිමට", "රැවුල"),
    ("0rder ID: 8", "0rder ID"),
    ("Toatl Rs 1,300", "Toatl"),
    ("Be Focus Your Look", "Be Focus Your Look"),
    ("කොණ්ඩෙය කැපිමට (පැසටිම)", "(පැසටිම)"),
]


def test_retrieval_hit_rate_on_labelled_queries():
    spatial = _mock_spatial_chunks()
    service = HashingEmbeddingService()
    rows = embed_rows(flatten_chunks_for_embedding(spatial), service=service)

    hits = 0
    for query, expected_substring in _LABELLED_QUERIES:
        query_vector = service.embed([query], mode="query")[0]
        top = rank_embedded_rows(query_vector, rows, k=1)[0]
        if expected_substring in top["text"]:
            hits += 1

    hit_rate = hits / len(_LABELLED_QUERIES)
    assert hit_rate == 1.0


def test_retrieval_beats_naive_whole_document_baseline():
    """A naive baseline (one chunk = the whole document's concatenated text,
    the pre-C2 behavior) can't distinguish between queries -- every query
    "retrieves" the same blob, so it can never point at a specific line item.
    Per-chunk retrieval can. This is the comparison docs/TESTING.md's "C4
    retrieval ... beats naive chunking baseline" metric carries forward from."""
    spatial = _mock_spatial_chunks()
    service = HashingEmbeddingService()
    chunk_rows = embed_rows(flatten_chunks_for_embedding(spatial), service=service)

    naive_text = " ".join(r["text"] for r in flatten_chunks_for_embedding(spatial))
    naive_rows = embed_rows([{"chunk_id": "whole_doc", "text": naive_text}], service=service)

    query = "රැවුල කැපිමට"
    query_vector = service.embed([query], mode="query")[0]

    chunk_top = rank_embedded_rows(query_vector, chunk_rows, k=1)[0]
    naive_top = rank_embedded_rows(query_vector, naive_rows, k=1)[0]

    # The naive baseline always returns the one whole-document chunk -- it
    # carries no chunk-level provenance, so even a "hit" only points at every
    # bbox on the page. Per-chunk retrieval narrows to the one line item.
    assert chunk_top["chunk_id"] != "whole_doc"
    assert "රැවුල" in chunk_top["text"]
    assert naive_top["chunk_id"] == "whole_doc"
    assert len(chunk_top["text"]) < len(naive_top["text"])


# ---------------------------------------------------------------------------
# pgvector-backed round trip (DB integration; skipped without DATABASE_URL,
# same pattern as tests/test_iter1_data_layer.py)
# ---------------------------------------------------------------------------

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))
except Exception:
    pass

pytestmark_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping vector DB integration tests",
)


@pytestmark_db
def test_upsert_and_retrieve_round_trip_with_tenant_isolation():
    import uuid

    import db
    from vector_index import retrieve_top_k, upsert_chunk_embeddings

    tenant_a = f"test_{uuid.uuid4().hex}"
    tenant_b = f"test_{uuid.uuid4().hex}"
    document_id = f"doc_{uuid.uuid4().hex}"
    service = HashingEmbeddingService()

    try:
        rows = embed_rows(
            [
                {"tenant_id": tenant_a, "document_id": document_id, "chunk_id": "ch_0",
                 "page": 1, "bbox": [0, 0, 10, 10], "chunk_type": "section_text",
                 "text": "රැවුල කැපිමට beard trim service"},
                {"tenant_id": tenant_a, "document_id": document_id, "chunk_id": "ch_1",
                 "page": 1, "bbox": [0, 10, 10, 20], "chunk_type": "section_text",
                 "text": "completely unrelated invoice footer text"},
            ],
            service=service,
        )
        upsert_chunk_embeddings(rows)

        results = retrieve_top_k("රැවුල කැපිමට", tenant_id=tenant_a, k=1, service=service)
        assert len(results) == 1
        assert results[0]["chunk_id"] == "ch_0"
        assert results[0]["bbox"] == [0, 0, 10, 10]

        # Tenant B must not see tenant A's chunks.
        assert retrieve_top_k("රැවුල කැපිමට", tenant_id=tenant_b, k=5, service=service) == []
    finally:
        with db.get_conn() as conn:
            conn.cursor().execute('DELETE FROM "ChunkEmbedding" WHERE "tenantId" = %s', (tenant_a,))
