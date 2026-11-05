"""PO -> Delivery Note -> Invoice -> Receipt linking.

Decides which of a tenant's documents belong to the same purchase/sale and
stores the result as DocLink rows (migration 012 adds DocLink.status):

    from (child)   to (parent)   linkType           reference field on the child
    ------------   -----------   ----------------   ----------------------------
    dn             po            po_dn              po_ref
    invoice        po            po_invoice         po_ref
    invoice        dn            dn_invoice         dn_ref
    receipt        invoice       invoice_receipt    invoice_ref

Scoring (score_pair) is evidence-based and pure. A link needs real identity
evidence -- a matching reference / numbering, or the same counterparty plus
matching items or amounts; dates alone never link documents.

    >= AUTO_THRESHOLD     status "auto"       linked automatically
    >= SUGGEST_THRESHOLD  status "suggested"  shown to the user to confirm
    otherwise             not linked

User decisions are sticky: "confirmed" links are never overwritten and
"rejected" pairs are never proposed again.

relink_tenant() recomputes a tenant's links (cheap: tens to hundreds of
documents) and is called after every save / edit / delete. load_chain()
returns the connected order around one document for order_matching.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Optional

from references import derive_references, ref_core, ref_key

AUTO_THRESHOLD = 0.8
SUGGEST_THRESHOLD = 0.45
# Two candidates this close to each other are ambiguous -> suggest, don't auto-link.
AMBIGUITY_MARGIN = 0.05

# (child type, parent type) -> (linkType, reference field on the child)
RELATIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("dn", "po"): ("po_dn", "po_ref"),
    ("invoice", "po"): ("po_invoice", "po_ref"),
    ("invoice", "dn"): ("dn_invoice", "dn_ref"),
    ("receipt", "invoice"): ("invoice_receipt", "invoice_ref"),
}
CHAIN_LINK_TYPES = {lt for lt, _ in RELATIONS.values()}
ACTIVE_STATUSES = ("auto", "confirmed")

_PURCHASE_FLOWS = {"payable", "cash_outflow"}
_SALE_FLOWS = {"receivable", "cash_inflow"}


# ── preparation ──────────────────────────────────────────────────────────────

def _num(value) -> float:
    try:
        text = re.sub(r"[^0-9.\-]", "", str(value or ""))
        return float(text) if text not in ("", ".", "-") else 0.0
    except ValueError:
        return 0.0


def parse_date(raw) -> Optional[date]:
    text = str(raw or "").strip()
    if not text or text.upper() in ("NULL", "NONE"):
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text[:20].strip(), fmt).date()
        except ValueError:
            continue
    return None


_WORD = re.compile(r"[a-z0-9඀-෿]+")
_ZERO_WIDTH = re.compile("[​‌‍﻿]")
_STOP = {"the", "of", "and", "for", "with", "each", "x"}
# Sizes, units and packaging: shared by many different products ("25kg bag"),
# so they count for little when deciding whether two lines are the same item.
_WEAK = {
    "kg", "kgs", "g", "gr", "gram", "grams", "ml", "l", "ltr", "litre", "liter", "m", "cm", "mm", "ft",
    "pcs", "pc", "nos", "no", "unit", "units", "box", "boxes", "pack", "packs", "packet", "packets",
    "bag", "bags", "bottle", "bottles", "roll", "rolls", "set", "sets",
    "කි", "කිලෝ", "ග්රෑ", "ග්රෑම්", "ලී", "මි", "මලු", "මල්ල", "පැකට්", "පැකට්ට", "බෝතල්", "පෙට්ටි",
}


def _clean(text) -> str:
    # Zero-width joiners inside Sinhala conjuncts (ග්‍රෑ) would split words.
    return _ZERO_WIDTH.sub("", str(text or "")).lower()


def item_tokens(description) -> frozenset:
    return frozenset(w for w in _WORD.findall(_clean(description)) if w not in _STOP)


def item_name(description) -> str:
    """The product-name part of a line ("සීනි මලු (කි.ග්‍රෑ. 25)" -> "සීනි"),
    without sizes, units or packaging -- for typo-tolerant comparison."""
    text = re.split(r"[(\[]", _clean(description), maxsplit=1)[0]
    words = [w for w in _WORD.findall(text)
             if w not in _STOP and w not in _WEAK and not any(ch.isdigit() for ch in w)]
    return " ".join(words)


def _w(token: str) -> float:
    return 0.3 if token in _WEAK or any(ch.isdigit() for ch in token) else 1.0


def token_similarity(a: frozenset, b: frozenset) -> float:
    """Weighted overlap of two token sets: max(Jaccard, containment), with
    sizes/units/packaging down-weighted so "Sugar 25kg bag" and "Rice 25kg
    bag" do not look alike."""
    if not a or not b:
        return 0.0
    shared = a & b
    inter = sum(_w(t) for t in shared)
    union = sum(_w(t) for t in a | b)
    if not union:
        return 0.0
    jaccard = inter / union
    # Containment ("Cement 50kg" inside "Cement 50kg bags (Tokyo Super)") only
    # counts when a real product word is shared, not just "25 kg bag".
    if not any(_w(t) == 1.0 for t in shared):
        return jaccard
    smaller = min(sum(_w(t) for t in a), sum(_w(t) for t in b))
    return max(jaccard, inter / smaller * 0.9) if smaller else jaccard


def item_similarity(x: dict, y: dict) -> float:
    """Are two line items the same product? Weighted token overlap, or a
    character-level comparison of the product names (absorbs OCR typos like
    සිති / සීනි)."""
    from difflib import SequenceMatcher

    tok = token_similarity(x["tokens"], y["tokens"])
    nx, ny = x.get("name") or "", y.get("name") or ""
    chars = SequenceMatcher(None, nx, ny).ratio() * 0.95 if nx and ny else 0.0
    return max(tok, chars)


def _items(record: dict) -> list[dict]:
    raw = record.get("items")
    if raw is None:
        try:
            raw = json.loads(record.get("items_json") or "[]")
        except Exception:
            raw = []
    out = []
    for it in raw or []:
        if not isinstance(it, dict):
            continue
        out.append({
            "description": str(it.get("description") or "").strip(),
            "tokens": item_tokens(it.get("description")),
            "name": item_name(it.get("description")),
            "qty": _num(it.get("quantity")),
            "unit_price": _num(it.get("unit_price")),
            "line_total": _num(it.get("line_total")),
        })
    return out


def _counterparty(record: dict) -> str:
    from entity_index import normalize_entity_name
    return normalize_entity_name(record.get("supplier_name") or "")


def prepare(record: dict) -> dict:
    """Everything scoring needs about one document, computed once."""
    refs = derive_references(record)
    doc_type = str(record.get("document_type") or "").strip().lower()
    total = _num(record.get("final_total_amount")) or _num(record.get("payable_amount"))
    return {
        "document_id": str(record.get("document_id")),
        "type": doc_type,
        "refs": refs,
        "own_key": ref_key(refs["doc_number"]) or (ref_key(record.get("order_id")) if doc_type == "po" else ""),
        "own_core": ref_core(refs["doc_number"]),
        "order_key": ref_key(record.get("order_id")),
        "counterparty": _counterparty(record),
        "date": parse_date(record.get("date")),
        "total": total,
        "flow": str(record.get("effective_flow_type") or record.get("flow_type") or "").strip().lower(),
        "items": _items(record),
        "record": record,
    }


# ── scoring ──────────────────────────────────────────────────────────────────

def _line_overlap(child: dict, parent: dict) -> float:
    """Share of the child's line items that appear on the parent."""
    if not child["items"] or not parent["items"]:
        return 0.0
    hits = 0
    for line in child["items"]:
        best = max((item_similarity(line, p) for p in parent["items"]), default=0.0)
        if best >= 0.5:
            hits += 1
    return hits / len(child["items"])


def score_pair(child: dict, parent: dict) -> tuple[float, list[str]]:
    """Confidence (0..0.99) that `child` belongs to `parent`, with reasons."""
    relation = RELATIONS.get((child["type"], parent["type"]))
    if not relation:
        return 0.0, []
    _, ref_field = relation
    reasons: list[str] = []

    # 1) identity from numbers -- take the strongest, don't stack them
    ref_signals = []
    child_ref = ref_key(child["refs"].get(ref_field))
    if child_ref and child_ref == parent["own_key"]:
        # "__new__" is the not-yet-saved document on the upload review screen.
        who = "This document" if child["document_id"] == "__new__" else (child["refs"].get("doc_number") or child["document_id"])
        ref_signals.append((0.7, f"{who} refers to {parent['refs']['doc_number'] or parent['document_id']}"))
    child_po, parent_po = ref_key(child["refs"].get("po_ref")), ref_key(parent["refs"].get("po_ref"))
    if parent["type"] != "po" and child_po and child_po == parent_po:
        ref_signals.append((0.35, "both refer to the same purchase order"))
    if child["order_key"] and child["order_key"] == parent["order_key"]:
        ref_signals.append((0.4, "same order number"))
    if child["own_core"] and child["own_core"] == parent["own_core"]:
        ref_signals.append((0.3, "matching document numbering"))
    ref_score, ref_reason = max(ref_signals, default=(0.0, ""))
    if ref_reason:
        reasons.append(ref_reason)
    score = ref_score

    # 2) same counterparty (or clearly a different one)
    party_match = False
    if child["counterparty"] and parent["counterparty"]:
        from entity_index import is_fuzzy_match
        if is_fuzzy_match(child["counterparty"], parent["counterparty"]):
            party_match = True
            score += 0.2
            reasons.append("same supplier/customer")
        else:
            score -= 0.35
            reasons.append("different supplier/customer")

    # 3) purchase vs sale should agree. Extraction sometimes gets the flow
    #    wrong, so a conflict doesn't forbid the link -- it means a person has
    #    to confirm it (capped below AUTO_THRESHOLD at the end).
    flow_conflict = (child["flow"] in _PURCHASE_FLOWS and parent["flow"] in _SALE_FLOWS) or \
                    (child["flow"] in _SALE_FLOWS and parent["flow"] in _PURCHASE_FLOWS)
    if flow_conflict:
        score -= 0.2
        reasons.append("one is a purchase, the other a sale")

    # 4) line items
    overlap = _line_overlap(child, parent)
    if overlap:
        score += 0.25 * overlap
        reasons.append(f"{round(overlap * 100)}% of items match")

    # 5) amounts
    amount_fit = False
    if child["total"] and parent["total"]:
        tol = max(1.0, 0.01 * parent["total"])
        if abs(child["total"] - parent["total"]) <= tol:
            amount_fit = True
            score += 0.25 if child["type"] == "receipt" else 0.15
            reasons.append("same amount")
        elif child["type"] == "receipt":
            if child["total"] < parent["total"]:
                score += 0.05
                reasons.append("partial payment of the invoice")
            elif child["total"] > parent["total"] * 1.05:
                score -= 0.2
                reasons.append("paid more than the invoice total")

    # 6) dates in a sensible order (parent first)
    if child["date"] and parent["date"]:
        gap = (child["date"] - parent["date"]).days
        if gap >= -3:
            score += 0.05
        elif gap < -30:
            score -= 0.2
            reasons.append("dated well before its parent")

    # Never link on circumstantial evidence alone.
    has_identity = ref_score > 0 or (party_match and (overlap >= 0.5 or amount_fit))
    if not has_identity:
        return 0.0, reasons
    if flow_conflict:
        score = min(score, AUTO_THRESHOLD - 0.1)
    return max(0.0, min(0.99, round(score, 3))), reasons


def propose_links(prepared: list[dict], decisions: dict | None = None) -> list[dict]:
    """Best parent per (child, relation) as link dicts:
    {from, to, link_type, confidence, status, reasons}.

    decisions: {(from, to, link_type): "confirmed"|"rejected"} from the user --
    confirmed links are returned as-is, rejected pairs are skipped, and a child
    with a confirmed parent for a relation gets no automatic alternative.
    """
    decisions = decisions or {}
    by_type: dict[str, list[dict]] = {}
    for p in prepared:
        by_type.setdefault(p["type"], []).append(p)

    links: list[dict] = []
    for (child_type, parent_type), (link_type, _) in RELATIONS.items():
        for child in by_type.get(child_type, []):
            confirmed = [
                to for (fr, to, lt), st in decisions.items()
                if fr == child["document_id"] and lt == link_type and st == "confirmed"
            ]
            if confirmed:
                continue  # the user already decided this relation
            scored = []
            for parent in by_type.get(parent_type, []):
                if decisions.get((child["document_id"], parent["document_id"], link_type)) == "rejected":
                    continue
                conf, reasons = score_pair(child, parent)
                if conf >= SUGGEST_THRESHOLD:
                    scored.append((conf, parent, reasons))
            if not scored:
                continue
            scored.sort(key=lambda s: -s[0])
            best_conf, best, best_reasons = scored[0]
            ambiguous = len(scored) > 1 and best_conf - scored[1][0] < AMBIGUITY_MARGIN
            status = "auto" if best_conf >= AUTO_THRESHOLD and not ambiguous else "suggested"
            links.append({
                "from": child["document_id"], "to": best["document_id"], "link_type": link_type,
                "confidence": best_conf, "status": status,
                "reasons": best_reasons + (["another document matches almost as well"] if ambiguous else []),
            })
            # Ambiguous: also surface the runner-up so the user can pick.
            if ambiguous:
                conf2, second, reasons2 = scored[1]
                links.append({
                    "from": child["document_id"], "to": second["document_id"], "link_type": link_type,
                    "confidence": conf2, "status": "suggested", "reasons": reasons2,
                })
    return links


# ── persistence ──────────────────────────────────────────────────────────────

def _load_decisions(cur, tenant_id: str) -> dict:
    cur.execute(
        'SELECT "fromDocId", "toDocId", "linkType", status FROM "DocLink"'
        ' WHERE "tenantId"=%s AND "linkType" = ANY(%s) AND status IN (\'confirmed\', \'rejected\')',
        (tenant_id, list(CHAIN_LINK_TYPES)),
    )
    return {(r["fromDocId"], r["toDocId"], r["linkType"]): r["status"] for r in cur.fetchall()}


def relink_tenant(user_id: str, dry_run: bool = False, records: list[dict] | None = None) -> dict:
    """Recompute every automatic/suggested chain link for a tenant.

    Leaves the user's confirmed/rejected decisions untouched. With dry_run the
    database is not written; the report says what would change.
    """
    from db import get_conn, new_id
    from psycopg.types.json import Jsonb

    if records is None:
        from dataset_manager import load_all_records
        records = load_all_records(user_id=user_id)
    prepared = [prepare(r) for r in records if str(r.get("document_type") or "").lower() in ("po", "dn", "invoice", "receipt")]

    with get_conn() as conn:
        cur = conn.cursor()
        decisions = _load_decisions(cur, user_id)
        proposed = propose_links(prepared, decisions)

        cur.execute(
            'SELECT "fromDocId", "toDocId", "linkType", status, confidence FROM "DocLink"'
            ' WHERE "tenantId"=%s AND "linkType" = ANY(%s) AND status IN (\'auto\', \'suggested\')',
            (user_id, list(CHAIN_LINK_TYPES)),
        )
        before = {(r["fromDocId"], r["toDocId"], r["linkType"]): r["status"] for r in cur.fetchall()}
        after = {(l["from"], l["to"], l["link_type"]): l["status"] for l in proposed}

        report = {
            "documents": len(prepared),
            "links": proposed,
            "added": [k for k in after if k not in before],
            "removed": [k for k in before if k not in after],
            "changed": [k for k in after if k in before and before[k] != after[k]],
            "kept_user_decisions": len(decisions),
        }
        if dry_run:
            return report

        cur.execute(
            'DELETE FROM "DocLink" WHERE "tenantId"=%s AND "linkType" = ANY(%s) AND status IN (\'auto\', \'suggested\')',
            (user_id, list(CHAIN_LINK_TYPES)),
        )
        if proposed:  # one batched round trip instead of one per link
            cur.executemany(
                'INSERT INTO "DocLink" (id, "tenantId", "fromDocId", "linkType", "toDocId", confidence, evidence, status, "createdAt")'
                ' VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())',
                [(new_id("lnk"), user_id, l["from"], l["link_type"], l["to"], l["confidence"],
                  Jsonb({"reasons": l["reasons"]}), l["status"]) for l in proposed],
            )
    return report


def set_link_decision(user_id: str, from_doc: str, to_doc: str, link_type: str, decision: str) -> None:
    """Record the user's decision on a link: "confirmed" (also used for links
    the user creates by hand) or "rejected"."""
    from db import get_conn, new_id
    from psycopg.types.json import Jsonb

    if decision not in ("confirmed", "rejected"):
        raise ValueError("decision must be 'confirmed' or 'rejected'")
    if link_type not in CHAIN_LINK_TYPES:
        raise ValueError(f"unknown link type {link_type!r}")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            'DELETE FROM "DocLink" WHERE "tenantId"=%s AND "fromDocId"=%s AND "toDocId"=%s AND "linkType"=%s',
            (user_id, from_doc, to_doc, link_type),
        )
        if decision == "confirmed":
            # A confirmed parent replaces any automatic alternative for this relation.
            cur.execute(
                'DELETE FROM "DocLink" WHERE "tenantId"=%s AND "fromDocId"=%s AND "linkType"=%s'
                ' AND status IN (\'auto\', \'suggested\')',
                (user_id, from_doc, link_type),
            )
        cur.execute(
            'INSERT INTO "DocLink" (id, "tenantId", "fromDocId", "linkType", "toDocId", confidence, evidence, status, "createdAt")'
            ' VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())',
            (new_id("lnk"), user_id, from_doc, link_type, to_doc, 1.0 if decision == "confirmed" else 0.0,
             Jsonb({"reasons": ["set by you"]}), decision),
        )


def relation_for(child_type: str, parent_type: str) -> Optional[str]:
    rel = RELATIONS.get((child_type, parent_type))
    return rel[0] if rel else None


def load_tenant_links(user_id: str) -> list[dict]:
    from db import get_conn
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT "fromDocId", "toDocId", "linkType", status, confidence, evidence FROM "DocLink"'
            ' WHERE "tenantId"=%s AND "linkType" = ANY(%s)',
            (user_id, list(CHAIN_LINK_TYPES)),
        )
        return [
            {"from": r["fromDocId"], "to": r["toDocId"], "link_type": r["linkType"], "status": r["status"],
             "confidence": float(r["confidence"] or 0),
             "reasons": (r["evidence"] or {}).get("reasons", []) if isinstance(r["evidence"], dict) else []}
            for r in cur.fetchall()
        ]


def chain_component(document_id: str, links: list[dict]) -> set[str]:
    """Document ids connected to document_id through active (auto/confirmed) links."""
    adj: dict[str, set[str]] = {}
    for l in links:
        if l["status"] in ACTIVE_STATUSES and l["to"]:
            adj.setdefault(l["from"], set()).add(l["to"])
            adj.setdefault(l["to"], set()).add(l["from"])
    seen, stack = {document_id}, [document_id]
    while stack:
        for nxt in adj.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen
