"""Component 4 — Multi-Tenant Relationship Index (Iteration 6).

Builds and queries a graph of entities (vendors, customers) and cross-document
links stored in the pre-existing C4 tables (Entity, EntityAlias, DocLink — added
in Iteration 1's schema migration, see docs/design/iter-1-schema.md).

Pipeline:
  index_document(doc, tenant_id)
    → extract supplier / company names
    → normalize_entity_name() — strip corp suffixes + punctuation
    → upsert Entity row (canonical form)
    → upsert EntityAlias row for the raw spelling
    → fuzzy-match against existing entities (is_fuzzy_match)
    → upsert EntityAlias for each fuzzy match found
    → upsert DocLink: fromDocId → entity (vendor_entity / customer_entity)
    → upsert DocLink: fromDocId → toDocId for shared order_id (order_ref)

Query APIs:
  expand_related_docs(doc_id, tenant_id)  → [doc_id, ...]
  filter_docs_by_entity(name, tenant_id)  → [doc_id, ...]

These are consumed by pal_scope.resolve_scope_with_c4() (Iteration 6 wiring).
"""
from __future__ import annotations

import re

from db import get_conn, new_id


# ── entity name normalisation ─────────────────────────────────────────────────

_CORP_SUFFIXES = frozenset({
    "ltd", "limited", "pvt", "pte", "inc", "incorporated", "corp", "corporation",
    "co", "llc", "plc", "gmbh", "srl", "bv", "ag", "sa", "pty",
    "company", "enterprises", "services", "solutions", "group", "trading",
})


def normalize_entity_name(raw: str) -> str:
    """Return a canonical entity name for matching and storage.

    Steps:
      1. Lowercase, strip surrounding whitespace.
      2. Remove common punctuation (commas, parens, ampersands, etc.).
      3. Strip common corporate suffixes (ltd, pvt, co, etc.).
      4. Collapse internal whitespace.

    Returns empty string for NULL / missing / whitespace-only input.
    """
    if not raw:
        return ""
    text = str(raw).strip()
    if text.upper() in ("NULL", "NONE", "N/A", ""):
        return ""
    text = text.lower()
    text = re.sub(r"[.,()&'\"/\\-]", " ", text)
    words = [w for w in text.split() if w not in _CORP_SUFFIXES]
    return " ".join(words).strip()


# ── fuzzy matching ────────────────────────────────────────────────────────────

def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def _edit_dist(a: str, b: str) -> int:
    n = len(b)
    dp = list(range(n + 1))
    for ca in a:
        prev, dp[0] = dp[0], dp[0] + 1
        for j, cb in enumerate(b, 1):
            prev, dp[j] = dp[j], min(prev + (ca != cb), dp[j] + 1, dp[j - 1] + 1)
    return dp[n]


def is_fuzzy_match(name_a: str, name_b: str, threshold: float = 0.8) -> bool:
    """Conservative fuzzy match (research §7): Jaccard ≥ threshold OR
    edit distance ≤ 2 for short names.  Both inputs must be normalized first."""
    if not name_a or not name_b:
        return False
    if name_a == name_b:
        return True
    if _jaccard(name_a, name_b) >= threshold:
        return True
    if len(name_a) <= 12 and len(name_b) <= 12:
        return _edit_dist(name_a, name_b) <= 2
    return False


# ── DB helpers (all run inside a caller-managed get_conn() context) ───────────

def _upsert_entity(conn, tenant_id: str, entity_type: str, canonical: str, raw: str) -> str:
    """Insert or fetch Entity; returns its id (cuid)."""
    cur = conn.cursor()
    cur.execute(
        'SELECT id FROM "Entity" WHERE "tenantId"=%s AND "entityType"=%s AND "canonicalName"=%s',
        (tenant_id, entity_type, canonical),
    )
    row = cur.fetchone()
    if row:
        return row["id"]
    eid = new_id("ent")
    cur.execute(
        'INSERT INTO "Entity" (id, "tenantId", "entityType", "canonicalName", "rawName", "createdAt")'
        " VALUES (%s,%s,%s,%s,%s,NOW())",
        (eid, tenant_id, entity_type, canonical, raw),
    )
    return eid


def _upsert_alias(conn, tenant_id: str, entity_ref: str, alias_text: str,
                  method: str, score: float | None = None) -> None:
    """Insert EntityAlias if it doesn't already exist."""
    cur = conn.cursor()
    cur.execute(
        'SELECT id FROM "EntityAlias" WHERE "tenantId"=%s AND "entityRef"=%s AND "aliasText"=%s',
        (tenant_id, entity_ref, alias_text),
    )
    if cur.fetchone():
        return
    cur.execute(
        'INSERT INTO "EntityAlias" (id, "tenantId", "entityRef", "aliasText", score, method, "createdAt")'
        " VALUES (%s,%s,%s,%s,%s,%s,NOW())",
        (new_id("eal"), tenant_id, entity_ref, alias_text, score, method),
    )


def _upsert_doc_link(conn, tenant_id: str, from_doc_id: str, link_type: str,
                     to_entity_ref: str | None = None, to_doc_id: str | None = None,
                     confidence: float = 1.0, evidence: dict | None = None) -> None:
    """Insert DocLink; skip if an identical row already exists."""
    from psycopg.types.json import Jsonb
    cur = conn.cursor()
    cur.execute(
        'SELECT id FROM "DocLink" WHERE "tenantId"=%s AND "fromDocId"=%s AND "linkType"=%s'
        ' AND "toEntityRef" IS NOT DISTINCT FROM %s AND "toDocId" IS NOT DISTINCT FROM %s',
        (tenant_id, from_doc_id, link_type, to_entity_ref, to_doc_id),
    )
    if cur.fetchone():
        return
    cur.execute(
        'INSERT INTO "DocLink" (id, "tenantId", "fromDocId", "linkType", "toEntityRef",'
        ' "toDocId", confidence, evidence, "createdAt") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())',
        (new_id("lnk"), tenant_id, from_doc_id, link_type, to_entity_ref,
         to_doc_id, confidence, Jsonb(evidence) if evidence else None),
    )


def _existing_entities(conn, tenant_id: str, entity_type: str) -> list[dict]:
    """Return all existing entities of a type for fuzzy-alias deduplication."""
    cur = conn.cursor()
    cur.execute(
        'SELECT id, "canonicalName" FROM "Entity" WHERE "tenantId"=%s AND "entityType"=%s',
        (tenant_id, entity_type),
    )
    return list(cur.fetchall())


# ── main indexing entry point ─────────────────────────────────────────────────

def index_document(doc: dict, tenant_id: str) -> dict:
    """Extract entities + edges from one FinancialDocument record and write to DB.

    Idempotent — safe to call multiple times on the same document.

    Returns a summary for logging / assertions:
      {"doc_id", "entities": [(type, canonical, id), ...], "links": N}
    """
    doc_id = str(doc.get("document_id", "") or "").strip()
    if not doc_id or doc_id.upper() in ("NULL", "NONE"):
        return {"doc_id": doc_id, "entities": [], "links": 0}

    supplier_raw = str(doc.get("supplier_name", "") or "")
    company_raw  = str(doc.get("company_name",  "") or "")
    order_id_raw = str(doc.get("order_id",      "") or "")

    entities_created: list[tuple] = []
    link_count = 0

    with get_conn() as conn:
        for entity_type, raw_name in (("vendor", supplier_raw), ("customer", company_raw)):
            canonical = normalize_entity_name(raw_name)
            if not canonical:
                continue

            eid = _upsert_entity(conn, tenant_id, entity_type, canonical, raw_name)
            entities_created.append((entity_type, canonical, eid))

            # Raw spelling alias (if different from canonical)
            raw_lower = raw_name.lower().strip()
            if raw_lower and raw_lower != canonical:
                _upsert_alias(conn, tenant_id, eid, raw_lower, method="raw", score=1.0)

            # Fuzzy aliases — link this entity to names already in the index
            for existing in _existing_entities(conn, tenant_id, entity_type):
                if existing["id"] == eid:
                    continue
                if is_fuzzy_match(canonical, existing["canonicalName"]):
                    score = _jaccard(canonical, existing["canonicalName"])
                    _upsert_alias(conn, tenant_id, existing["id"], canonical,
                                  method="fuzzy", score=round(score, 3))

            link_type = "vendor_entity" if entity_type == "vendor" else "customer_entity"
            _upsert_doc_link(
                conn, tenant_id, doc_id, link_type,
                to_entity_ref=eid,
                evidence={"source_field": "supplier_name" if entity_type == "vendor" else "company_name",
                          "raw_value": raw_name},
            )
            link_count += 1

        # Cross-document edge via shared order_id
        order_id = order_id_raw.strip()
        if order_id and order_id.upper() not in ("NULL", "NONE", ""):
            cur = conn.cursor()
            cur.execute(
                'SELECT "documentId" FROM "FinancialDocument"'
                ' WHERE "tenantId"=%s AND "orderId"=%s AND "documentId"!=%s AND "deletedAt" IS NULL',
                (tenant_id, order_id, doc_id),
            )
            for row in cur.fetchall():
                other_id = row["documentId"]
                _upsert_doc_link(
                    conn, tenant_id, doc_id, "order_ref",
                    to_doc_id=other_id, confidence=0.9,
                    evidence={"order_id": order_id},
                )
                link_count += 1

    return {"doc_id": doc_id, "entities": entities_created, "links": link_count}


# ── query APIs ────────────────────────────────────────────────────────────────

def expand_related_docs_many(doc_ids: list[str], tenant_id: str) -> set[str]:
    """Batched expand_related_docs: every doc reachable from ANY of doc_ids via
    a DocLink edge (forward/reverse order_ref, or a shared vendor/customer
    entity), in a single round trip.

    The per-document version issues 3+ queries per document; resolve_scope_with_c4
    called it in a loop over the whole document set on every agent tool call,
    which cost ~0.4 s per document against a remote Postgres (4 s for 10 docs,
    and growing linearly). Same edges, same result set (as a union).
    """
    ids = [str(d) for d in doc_ids if d]
    if not ids:
        return set()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT "toDocId" AS doc FROM "DocLink"'
            ' WHERE "tenantId"=%(t)s AND "fromDocId" = ANY(%(ids)s) AND "toDocId" IS NOT NULL'
            "  AND COALESCE(status, 'auto') <> 'rejected'"
            ' UNION'
            ' SELECT "fromDocId" AS doc FROM "DocLink"'
            ' WHERE "tenantId"=%(t)s AND "toDocId" = ANY(%(ids)s)'
            "  AND COALESCE(status, 'auto') <> 'rejected'"
            ' UNION'
            ' SELECT peer."fromDocId" AS doc FROM "DocLink" src'
            ' JOIN "DocLink" peer ON peer."tenantId" = src."tenantId"'
            '  AND peer."toEntityRef" = src."toEntityRef"'
            ' WHERE src."tenantId"=%(t)s AND src."fromDocId" = ANY(%(ids)s)'
            '  AND src."toEntityRef" IS NOT NULL',
            {"t": tenant_id, "ids": ids},
        )
        return {r["doc"] for r in cur.fetchall() if r.get("doc")}


def expand_related_docs(doc_id: str, tenant_id: str) -> list[str]:
    """Return application doc IDs reachable from doc_id via any DocLink edge.

    Follows:
      - direct order_ref edges (fromDocId → toDocId)
      - reverse order_ref edges (toDocId → fromDocId)
      - entity peer links: other docs that share a vendor/customer entity

    Returns a deduplicated list, never containing doc_id itself.
    """
    related: set[str] = set()

    with get_conn() as conn:
        cur = conn.cursor()

        # Forward direct edges
        cur.execute(
            'SELECT "toDocId" FROM "DocLink"'
            ' WHERE "tenantId"=%s AND "fromDocId"=%s AND "toDocId" IS NOT NULL',
            (tenant_id, doc_id),
        )
        related.update(r["toDocId"] for r in cur.fetchall())

        # Reverse direct edges
        cur.execute(
            'SELECT "fromDocId" FROM "DocLink"'
            ' WHERE "tenantId"=%s AND "toDocId"=%s',
            (tenant_id, doc_id),
        )
        related.update(r["fromDocId"] for r in cur.fetchall())

        # Entity-peer links: docs sharing the same vendor/customer
        cur.execute(
            'SELECT "toEntityRef" FROM "DocLink"'
            ' WHERE "tenantId"=%s AND "fromDocId"=%s AND "toEntityRef" IS NOT NULL',
            (tenant_id, doc_id),
        )
        entity_refs = [r["toEntityRef"] for r in cur.fetchall()]
        for eref in entity_refs:
            cur.execute(
                'SELECT "fromDocId" FROM "DocLink"'
                ' WHERE "tenantId"=%s AND "toEntityRef"=%s AND "fromDocId"!=%s',
                (tenant_id, eref, doc_id),
            )
            related.update(r["fromDocId"] for r in cur.fetchall())

    related.discard(doc_id)
    return sorted(related)


def filter_docs_by_entity(entity_name: str, tenant_id: str) -> list[str]:
    """Return app doc IDs for documents linked to any entity matching entity_name.

    First tries an exact canonical match; falls back to a LIKE alias search.
    """
    canonical = normalize_entity_name(entity_name)
    if not canonical:
        return []

    entity_ids: list[str] = []

    with get_conn() as conn:
        cur = conn.cursor()

        # Exact canonical match
        cur.execute(
            'SELECT id FROM "Entity" WHERE "tenantId"=%s AND "canonicalName"=%s',
            (tenant_id, canonical),
        )
        entity_ids = [r["id"] for r in cur.fetchall()]

        # Alias fallback
        if not entity_ids:
            cur.execute(
                'SELECT DISTINCT "entityRef" FROM "EntityAlias"'
                ' WHERE "tenantId"=%s AND "aliasText" ILIKE %s',
                (tenant_id, f"%{canonical}%"),
            )
            entity_ids = [r["entityRef"] for r in cur.fetchall()]

        doc_ids: set[str] = set()
        for eid in entity_ids:
            cur.execute(
                'SELECT "fromDocId" FROM "DocLink" WHERE "tenantId"=%s AND "toEntityRef"=%s',
                (tenant_id, eid),
            )
            doc_ids.update(r["fromDocId"] for r in cur.fetchall())

    return sorted(doc_ids)
