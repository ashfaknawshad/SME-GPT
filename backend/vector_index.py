"""Vector indexing & retrieval over Component-2 SpatialChunks (Iteration 4).

Embeds each SpatialChunk's `text` (from `spatial_serialization.build_spatial_chunks()` output) via
an `EmbeddingService` and stores it in the `ChunkEmbedding` table (pgvector; proposed in
docs/design/iter-4-schema.md) with tenant/document/chunk metadata + bbox provenance carried
straight from the chunk's `provenance` block (FR-14, FR-15). Retrieval does a single pgvector
cosine-distance query, filtered by tenant (and optionally one document), returning chunks ranked
by similarity with their original provenance intact (FR-16, FR-17).

DB-touching functions (`upsert_chunk_embeddings`, `retrieve_top_k`) need a real Postgres
connection (`db.get_conn()`) and the `ChunkEmbedding` table from docs/design/iter-4-schema.md —
same `DATABASE_URL`-skip pattern as `tests/test_iter1_data_layer.py`. The chunk-flattening and
ranking math underneath (`flatten_chunks_for_embedding`, `rank_embedded_rows`) is pure and is
unit-tested without a DB, using `HashingEmbeddingService` so the suite stays hermetic.

WIRED LIVE (Iteration 9): the confirm-save flow (app.py::_post_save_enrich) builds SpatialChunks
from the C1 safe boxes, embeds them, and upserts here; retrieval feeds the query engine's scope
resolver (pal_scope.resolve_scope_with_rag), used by both /ask-query and the agent's
search_documents tool. Retrieval is hybrid dense+lexical (RRF) with an optional cross-encoder
rerank -- see retrieve_top_k below.
"""
from __future__ import annotations

import math
import os
import re as _re

from embedding_service import EmbeddingService, get_embedding_service

# ── Retrieval-quality config (all env-tunable) ───────────────────────────────
# Hybrid dense+lexical fusion is ON by default: dense embeddings are weak at
# exact tokens (document ids, numbers, exact Sinhala words) that lexical search
# nails, and vice-versa -- fusing them strictly improves recall over either
# alone. HNSW ef_search trades a little latency for recall on the ANN index.
RAG_HYBRID = os.getenv("RAG_HYBRID", "true").strip().lower() == "true"
RAG_HNSW_EF_SEARCH = int(os.getenv("RAG_HNSW_EF_SEARCH", "80"))
_RRF_C = int(os.getenv("RAG_RRF_C", "60"))  # reciprocal-rank-fusion damping constant


# Word chars PLUS the full Sinhala block (U+0D80–U+0DFF): plain \w drops Sinhala
# combining vowel signs (Mn/Mc), which would fragment a word like "රැවුල" at each
# mark. Including the block keeps Sinhala words whole so they tokenize the same
# way Postgres's 'simple' config does.
_WORD_RE = _re.compile(r"[\w඀-෿]+", _re.UNICODE)


def _or_tsquery(text: str) -> str:
    """Build an OR tsquery ('tok1 | tok2 | ...') from a free-text string.

    The lexical arm is a RECALL arm feeding RRF, so ANY-term matching is what we
    want (websearch/plainto AND-join every term, which misses a passage unless it
    contains all of them -- e.g. "terms and conditions" would require the literal
    word "and"). Tokens are restricted to word/Sinhala runs so the string is
    always a valid, injection-safe to_tsquery input."""
    tokens = _WORD_RE.findall(text or "")
    return " | ".join(tokens)


def _expand_query_for_lexical(query: str) -> str:
    """Bilingual query expansion for the lexical arm only. data_tools.normalize_query
    maps ~40 Sinhala finance phrases to their English equivalents; OR-ing the
    normalized form with the original widens exact-match recall across the
    EN/Si mixed chunk store without touching the (already multilingual) dense
    arm. Best-effort -- falls back to the raw query."""
    try:
        from data_tools import normalize_query
        normalized = normalize_query(query)
    except Exception:
        normalized = query
    if normalized and normalized.strip().lower() != query.strip().lower():
        return f"{query} {normalized}"
    return query


def rrf_merge(ranked_lists: list[list[dict]], k: int, key: str = "chunk_id", c: int = _RRF_C) -> list[dict]:
    """Reciprocal Rank Fusion of several ranked candidate lists (pure, DB-free
    -> unit-testable). Each item's fused score is sum(1 / (c + rank)) across the
    lists it appears in (rank is 0-based). Returns the merged list, highest
    fused score first, de-duplicated by `key`, truncated to k."""
    fused: dict = {}
    scores: dict = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            item_key = item.get(key)
            if item_key is None:
                continue
            scores[item_key] = scores.get(item_key, 0.0) + 1.0 / (c + rank)
            # keep the richest copy (first-seen, then merged fields)
            fused[item_key] = {**fused.get(item_key, {}), **item}
    ordered = sorted(fused.values(), key=lambda it: scores[it[key]], reverse=True)
    for it in ordered:
        it["fusion_score"] = round(scores[it[key]], 6)
    return ordered[:k]


def flatten_chunks_for_embedding(spatial_chunks: dict) -> list[dict]:
    """`spatial_chunks`: `build_spatial_chunks()` output. Returns one row per
    chunk: `{tenant_id, document_id, chunk_id, page, bbox, chunk_type, text}`."""
    tenant_id = spatial_chunks["tenant_id"]
    document_id = spatial_chunks["document_id"]
    rows = []
    for page in spatial_chunks.get("pages", []):
        for chunk in page.get("chunks", []):
            rows.append({
                "tenant_id": tenant_id,
                "document_id": document_id,
                "chunk_id": chunk["chunk_id"],
                "page": chunk["provenance"]["page"],
                "bbox": chunk["provenance"]["bbox"],
                "chunk_type": chunk["chunk_type"],
                "text": chunk["text"],
            })
    return rows


def embed_rows(rows: list[dict], service: EmbeddingService | None = None) -> list[dict]:
    """Attach an `embedding` (list[float]) to each row via the EmbeddingService."""
    service = service or get_embedding_service()
    if not rows:
        return []
    vectors = service.embed([r["text"] for r in rows], mode="passage")
    return [{**row, "embedding": vec} for row, vec in zip(rows, vectors)]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)


def rank_embedded_rows(query_vector: list[float], rows: list[dict], k: int = 5) -> list[dict]:
    """Pure, DB-free ranking: cosine similarity of `query_vector` against each
    row's `embedding`, descending, top-k. Mirrors the pgvector `<=>` query in
    `retrieve_top_k` below, so it doubles as the retrieval-hit-rate test harness
    (ROADMAP Iteration 4) without needing a live Postgres connection."""
    scored = [
        {**{key: v for key, v in row.items() if key != "embedding"},
         "similarity": _cosine_similarity(query_vector, row["embedding"])}
        for row in rows
    ]
    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return scored[:k]


def upsert_chunk_embeddings(rows: list[dict]) -> int:
    """Write embedded rows (from `embed_rows`) to the `ChunkEmbedding` table.
    Requires a real `DATABASE_URL` and the migration in
    docs/design/iter-4-schema.md to have been applied. Returns the row count."""
    import db
    from pgvector.psycopg import register_vector
    from psycopg.types.json import Json

    with db.get_conn() as conn:
        register_vector(conn)
        cur = conn.cursor()
        for row in rows:
            cur.execute(
                """
                INSERT INTO "ChunkEmbedding"
                    ("id", "tenantId", "documentId", "chunkId", "page", "bbox", "chunkType", "text", "embedding")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ("tenantId", "documentId", "chunkId")
                DO UPDATE SET "page" = EXCLUDED."page", "bbox" = EXCLUDED."bbox",
                              "chunkType" = EXCLUDED."chunkType", "text" = EXCLUDED."text",
                              "embedding" = EXCLUDED."embedding"
                """,
                (
                    db.new_id("ce"), row["tenant_id"], row["document_id"], row["chunk_id"],
                    row["page"], Json(row["bbox"]), row["chunk_type"], row["text"], row["embedding"],
                ),
            )
        return len(rows)


def _chunk_type_clause(chunk_types: list[str] | None, params: list) -> str:
    if not chunk_types:
        return ""
    params.append(list(chunk_types))
    return ' AND "chunkType" = ANY(%s)'


def _dense_candidates(cur, query_vector, tenant_id, n, document_id, chunk_types) -> list[dict]:
    sql = """
        SELECT "chunkId" AS chunk_id, "documentId" AS document_id, "page", "bbox",
               "chunkType" AS chunk_type, "text",
               1 - ("embedding" <=> %s::vector) AS similarity
        FROM "ChunkEmbedding"
        WHERE "tenantId" = %s
    """
    params: list = [query_vector, tenant_id]
    if document_id:
        sql += ' AND "documentId" = %s'
        params.append(document_id)
    sql += _chunk_type_clause(chunk_types, params)
    sql += ' ORDER BY "embedding" <=> %s::vector LIMIT %s'
    params.extend([query_vector, n])
    cur.execute(sql, params)
    return cur.fetchall()


def _lexical_candidates(cur, query_text, tenant_id, n, document_id, chunk_types) -> list[dict]:
    """Postgres full-text arm using the language-agnostic 'simple' config (no
    English stemming that would mangle Sinhala tokens) with OR-term matching."""
    tsquery = _or_tsquery(query_text)
    if not tsquery:
        return []
    sql = """
        SELECT "chunkId" AS chunk_id, "documentId" AS document_id, "page", "bbox",
               "chunkType" AS chunk_type, "text",
               ts_rank(to_tsvector('simple', "text"),
                       to_tsquery('simple', %s)) AS lex_rank
        FROM "ChunkEmbedding"
        WHERE "tenantId" = %s
          AND to_tsvector('simple', "text") @@ to_tsquery('simple', %s)
    """
    params: list = [tsquery, tenant_id, tsquery]
    if document_id:
        sql += ' AND "documentId" = %s'
        params.append(document_id)
    sql += _chunk_type_clause(chunk_types, params)
    sql += ' ORDER BY lex_rank DESC LIMIT %s'
    params.append(n)
    try:
        cur.execute("SAVEPOINT lex")
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.execute("RELEASE SAVEPOINT lex")
        return rows
    except Exception as exc:
        # Malformed tsquery / FTS unavailable -> dense-only (best effort). Roll
        # back to the savepoint so the dense arm's transaction stays usable.
        print(f"[RAG] lexical arm skipped ({exc}).", flush=True)
        try:
            cur.execute("ROLLBACK TO SAVEPOINT lex")
        except Exception:
            pass
        return []


def retrieve_top_k(
    query: str,
    tenant_id: str,
    k: int = 5,
    document_id: str | None = None,
    service: EmbeddingService | None = None,
    *,
    chunk_types: list[str] | None = None,
    hybrid: bool | None = None,
    rerank: bool | None = None,
) -> list[dict]:
    """Retrieve the top-k most relevant chunks for `tenant_id`, each with its
    page/bbox/chunk_type provenance (FR-16, FR-17).

    Layers (all safe to disable, each strictly additive to recall/precision):
      - dense ANN over pgvector (HNSW, ef_search tuned via RAG_HNSW_EF_SEARCH)
      - hybrid lexical fusion (RRF) -- default RAG_HYBRID; nails exact tokens
        (ids/numbers/exact Sinhala words) that dense embeddings miss
      - optional cross-encoder rerank (RAG_RERANK, off by default -- precision
        at the cost of latency)
      - optional metadata pre-filter by `chunk_types`
    """
    service = service or get_embedding_service()
    use_hybrid = RAG_HYBRID if hybrid is None else hybrid
    # A remote embedding engine (Colab) can be unreachable. Rather than losing
    # retrieval entirely, drop the dense arm and answer from the lexical arm --
    # which needs no embedding. Without hybrid there is nothing left, so re-raise
    # and let callers degrade to SQL-only scope as before.
    try:
        query_vector = service.embed([query], mode="query")[0]
    except Exception as exc:
        if not use_hybrid:
            raise
        print(f"[RAG] query embedding failed ({exc}); lexical-only retrieval.", flush=True)
        query_vector = None

    # Over-fetch so fusion/rerank have a candidate pool wider than the final k.
    pool = max(k * 4, 20)

    import db
    from pgvector.psycopg import register_vector

    with db.get_conn() as conn:
        register_vector(conn)
        cur = conn.cursor()
        # SET does not accept bind parameters -> inline the validated int (safe:
        # RAG_HNSW_EF_SEARCH is int()-parsed from env). Wrapped in a savepoint so
        # an unsupported GUC on older pgvector can't poison the transaction.
        try:
            cur.execute("SAVEPOINT ef")
            cur.execute(f"SET LOCAL hnsw.ef_search = {int(RAG_HNSW_EF_SEARCH)}")
            cur.execute("RELEASE SAVEPOINT ef")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT ef")
        dense = (
            _dense_candidates(cur, query_vector, tenant_id, pool, document_id, chunk_types)
            if query_vector is not None else []
        )
        if use_hybrid:
            lexical = _lexical_candidates(
                cur, _expand_query_for_lexical(query), tenant_id, pool, document_id, chunk_types
            )
        else:
            lexical = []

    if use_hybrid and lexical:
        candidates = rrf_merge([dense, lexical], k=pool)
    else:
        candidates = dense

    from rerank_service import rerank as _rerank, rerank_enabled
    if (rerank if rerank is not None else rerank_enabled()):
        return _rerank(query, candidates, top_k=k)
    return candidates[:k]
