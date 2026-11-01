"""Order reconciliation — the PO → DN → Invoice state machine.

Each document carries a status derived only from its OWN fields
(correction_engine._derive_workflow_status). This module adds the
cross-document layer: documents that belong to the same purchase order form one
*order*, and the Purchase Order's delivery status is reconciled from the
Delivery Notes booked against it.

LINKING. A document's link key is the purchase-order number it belongs to:
  * a PO links by its own number (`order_id`);
  * a DN or invoice links by the PO number printed on it (`po_reference`),
    falling back to its `order_id` for records saved before `po_reference`
    existed (when `order_id` held the PO number).
Keys are compared after `order_key()` normalisation, so "PO-2026-004",
"po 2026/004" and "2026-004" are the same order.

FULFILMENT (locked with the product owner):
  * By QUANTITY. A PO is `fulfilled` once the Delivery Notes marked delivered
    together cover every PO line item in full (items matched by description).
    Any delivery short of that is `partially_delivered`.
  * When quantities can't be compared — the PO has no quantified items, or a
    delivered DN lists none — it falls back to DN status: every DN delivered →
    `fulfilled`, some → `partially_delivered`.
  * Payment is tracked on the invoice and does NOT gate fulfilment.

RECOMPUTE, ALLOW DOWNGRADE. The status always reflects the current delivery
evidence: when a DN is un-delivered, deleted or moved to another order, a
`fulfilled`/`partially_delivered` PO falls back to `approved` (if it has an
approver) or `pending`. Two exceptions keep manual decisions intact:
  * `rejected` / `cancelled` are never overwritten;
  * a PO that has never had a Delivery Note linked keeps whatever status it was
    given — unless the caller says delivery evidence was just removed.

PERSIST. The reconciled `po_status` is written back to the PO so repository
chips, reports and chat all agree.

`reconcile_po_status` and `compute_delivery` are pure (no I/O) and fully
unit-testable; `reconcile_order` / `build_order_view` wrap them with
tenant-scoped DB reads and the single persisted write.
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, Optional

# Manually-set terminal PO states the cascade must never clobber.
_PO_TERMINAL = {"rejected", "cancelled"}
# Statuses this module sets; only these are reverted when evidence disappears.
_PO_DELIVERY_STATES = {"fulfilled", "partially_delivered"}

# A DN's delivery is expressed two ways depending on how it was set: the
# extraction pipeline derives `dn_status` (delivered / partially_delivered),
# while the analysis-page Edit form sets the DN's `received_status` to
# "delivered"/"not_delivered" and does NOT re-derive dn_status. Reconciliation
# therefore reads BOTH so a user marking a DN delivered cascades to its PO.
_DELIVERED_TOKENS = {"delivered", "received"}
_PARTIAL_TOKENS = {"partially_delivered", "partial", "partially_received"}

_NULLISH = {"", "null", "none", "nan", "n/a", "na", "-"}

# Two item descriptions at least this similar are treated as the same product
# (absorbs OCR noise like "A4 Paper 500 sheets" vs "A4 Paper (500 sheet)").
_ITEM_MATCH_THRESHOLD = 0.75
_QTY_EPSILON = 1e-6


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _is_nullish(value) -> bool:
    return _norm(value) in _NULLISH


# ── Linking ───────────────────────────────────────────────────────────────────

def order_key(value) -> str:
    """Canonical form of a purchase-order number, for comparing references.

    Upper-cases, drops every separator, and strips a leading "PO" /
    "PO No" / "Purchase Order No" label, so "PO-2026-004", "po 2026/004",
    "P.O. No: 2026-004" and "2026-004" all become "2026004". Returns "" for
    empty / NULL-ish values (which never link anything)."""
    if _is_nullish(value):
        return ""
    key = re.sub(r"[^A-Z0-9]", "", str(value).upper())
    stripped = re.sub(r"^(PURCHASEORDER|PO)(NUMBER|NO)?", "", key)
    # Only drop the label when a number remains ("PO" alone stays "PO").
    if any(ch.isdigit() for ch in stripped):
        key = stripped
    return key


def _po_reference(record: dict) -> str:
    """The PO number a DN/invoice refers to. Read from the `po_reference`
    column, falling back to the full structured JSON (which always holds the
    extraction output) so linking works even before the column migration."""
    ref = record.get("po_reference")
    if not _is_nullish(ref):
        return str(ref).strip()
    raw = record.get("structured_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    if isinstance(raw, dict) and not _is_nullish(raw.get("po_reference")):
        return str(raw["po_reference"]).strip()
    return ""


def link_key(record: dict) -> str:
    """The order (normalised PO number) a document belongs to, or ""."""
    if _norm(record.get("document_type")) == "po":
        return order_key(record.get("order_id"))
    return order_key(_po_reference(record)) or order_key(record.get("order_id"))


# ── Delivery evaluation (pure) ────────────────────────────────────────────────

def _dn_delivery(dn: dict) -> str:
    """A Delivery Note's progress: 'delivered' | 'partial' | 'none'.
    Reads dn_status first (pipeline-derived) then falls back to received_status
    (what the Edit form sets). failed / returned / delayed / pending are 'none'."""
    for field in ("dn_status", "received_status"):
        v = _norm(dn.get(field))
        if v in _DELIVERED_TOKENS:
            return "delivered"
        if v in _PARTIAL_TOKENS:
            return "partial"
    return "none"


def _qty(value) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _item_key(description) -> str:
    """Description normalised for matching: case-folded, punctuation dropped.
    Combining marks are kept so Sinhala vowel signs survive."""
    text = unicodedata.normalize("NFC", str(description or "")).casefold()
    kept = "".join(
        ch if ch.isalnum() or unicodedata.category(ch).startswith("M") else " "
        for ch in text
    )
    return " ".join(kept.split())


def _quantified_items(record: dict) -> list[tuple[str, float, str]]:
    """[(key, qty, description)] for the record's items with a positive qty."""
    out = []
    for item in record.get("items") or []:
        if not isinstance(item, dict):
            continue
        qty = _qty(item.get("quantity"))
        key = _item_key(item.get("description"))
        if qty > 0 and key:
            out.append((key, qty, str(item.get("description") or "").strip()))
    return out


def _best_po_line(key: str, po_keys: list[str]) -> Optional[str]:
    if key in po_keys:
        return key
    best, best_score = None, 0.0
    for candidate in po_keys:
        score = SequenceMatcher(None, key, candidate).ratio()
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= _ITEM_MATCH_THRESHOLD else None


def compute_delivery(po: Optional[dict], delivery_notes: list[dict]) -> dict:
    """How much of a PO has been delivered, from its Delivery Notes.

    Returns {stage: 'none'|'partial'|'full', mode: 'quantity'|'status',
    lines: [{description, ordered, delivered}], unmatched: [{description, quantity}]}.
    Pure — no I/O."""
    progress = [_dn_delivery(dn) for dn in delivery_notes]
    delivered_dns = [dn for dn, p in zip(delivery_notes, progress) if p == "delivered"]
    any_movement = any(p != "none" for p in progress)

    # Ordered quantity per product (duplicate PO lines are summed).
    ordered: dict[str, float] = {}
    labels: dict[str, str] = {}
    for key, qty, desc in (_quantified_items(po) if po else []):
        ordered[key] = ordered.get(key, 0.0) + qty
        labels.setdefault(key, desc)

    use_quantities = bool(ordered) and bool(delivered_dns) and all(
        _quantified_items(dn) for dn in delivered_dns
    )

    if not use_quantities:
        if delivery_notes and len(delivered_dns) == len(delivery_notes):
            stage = "full"
        elif any_movement:
            stage = "partial"
        else:
            stage = "none"
        return {"stage": stage, "mode": "status", "lines": [], "unmatched": []}

    # Only DNs marked fully delivered count towards quantities: a DN marked
    # partially delivered may list quantities that did not all arrive.
    received = {key: 0.0 for key in ordered}
    unmatched = []
    po_keys = list(ordered)
    for dn in delivered_dns:
        for key, qty, desc in _quantified_items(dn):
            line = _best_po_line(key, po_keys)
            if line is None:
                unmatched.append({"description": desc, "quantity": qty})
            else:
                received[line] += qty

    complete = all(received[k] + _QTY_EPSILON >= ordered[k] for k in ordered)
    if complete:
        stage = "full"
    elif any_movement:
        stage = "partial"
    else:
        stage = "none"

    lines = [
        {"description": labels[k], "ordered": ordered[k], "delivered": received[k]}
        for k in ordered
    ]
    return {"stage": stage, "mode": "quantity", "lines": lines, "unmatched": unmatched}


def _base_po_status(po: dict) -> str:
    """The status a PO falls back to when its delivery evidence disappears."""
    return "approved" if not _is_nullish(po.get("approved_by")) else "pending"


def reconcile_po_status(
    po: dict, delivery_notes: list[dict], evidence_removed: bool = False
) -> Optional[str]:
    """Return the reconciled `po_status` for a PO given the Delivery Notes in
    its order, or None when nothing should change. Pure.

      * rejected / cancelled -> None (manual decision, untouched).
      * No DNs linked -> None, unless `evidence_removed` (a DN was just
        deleted or moved away), in which case a delivery-derived status reverts.
      * Delivery complete -> "fulfilled"; some delivered -> "partially_delivered".
      * Nothing delivered -> a delivery-derived status reverts to approved /
        pending; any other status is left alone.
    A result equal to the PO's current status returns None (no-op)."""
    current = _norm(po.get("po_status"))
    if current in _PO_TERMINAL:
        return None
    if not delivery_notes and not evidence_removed:
        return None

    stage = compute_delivery(po, delivery_notes)["stage"]
    if stage == "full":
        new_status = "fulfilled"
    elif stage == "partial":
        new_status = "partially_delivered"
    elif current in _PO_DELIVERY_STATES:
        new_status = _base_po_status(po)
    else:
        return None

    return new_status if new_status != current else None


# ── DB-backed wrappers ────────────────────────────────────────────────────────

def _classify(records: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Split an order's records into (purchase_orders, delivery_notes, invoices)."""
    pos, dns, invoices = [], [], []
    for r in records:
        dt = _norm(r.get("document_type"))
        if dt == "po":
            pos.append(r)
        elif dt == "dn":
            dns.append(r)
        elif dt == "invoice":
            invoices.append(r)
    return pos, dns, invoices


def _records_for_key(key: str, user_id: str) -> list[dict]:
    """All of the tenant's records whose link key is `key` (already normalised:
    order_key is not idempotent for keys that start with "PO")."""
    from dataset_manager import load_all_records

    if not key:
        return []
    return [r for r in load_all_records(user_id=user_id) if link_key(r) == key]


def _order_records(order_id: str, user_id: str) -> list[dict]:
    """All of the tenant's records that belong to this order (raw PO number)."""
    return _records_for_key(order_key(order_id), user_id)


def _is_paid(invoices: list[dict]) -> bool:
    """True when at least one linked invoice is fully paid."""
    return any(_norm(inv.get("paid_status")) == "paid" for inv in invoices)


def _clean_status(value) -> Optional[str]:
    return None if _is_nullish(value) else _norm(value)


def build_order_view(order_id: str, user_id: str) -> Optional[dict]:
    """Snapshot of an order: its documents (PO first, then DNs, then invoices),
    the reconciled PO status, delivery progress per line item, and
    invoice/payment flags. Returns None if the order has no documents.
    Does NOT write."""
    return _build_view(order_key(order_id), user_id, order_id)


def order_view_for_document(record: dict, user_id: str) -> Optional[dict]:
    """The order a document belongs to (via its link key), or None."""
    key = link_key(record)
    return _build_view(key, user_id, str(record.get("order_id") or key)) if key else None


def _build_view(key: str, user_id: str, order_id: str) -> Optional[dict]:
    records = _records_for_key(key, user_id)
    if not records:
        return None

    pos, dns, invoices = _classify(records)
    anchor = pos[0] if pos else None
    delivery = compute_delivery(anchor, dns)

    # Reconciled PO status (may differ from what's stored until the next
    # reconcile persists it). The first PO is the order's anchor.
    reconciled_po = None
    if anchor:
        proposed = reconcile_po_status(anchor, dns)
        reconciled_po = proposed or _clean_status(anchor.get("po_status")) or "pending"

    def _summ(r: dict) -> dict:
        return {
            "document_id": r.get("document_id"),
            "document_type": _norm(r.get("document_type")),
            "date": r.get("date"),
            "supplier_name": r.get("supplier_name") or r.get("company_name") or "",
            "amount": r.get("final_total_amount") or r.get("payable_amount") or "",
            "currency": r.get("currency") or "LKR",
            "po_status": _clean_status(r.get("po_status")),
            "dn_status": _clean_status(r.get("dn_status")),
            "invoice_status": _clean_status(r.get("invoice_status")),
            "paid_status": _clean_status(r.get("paid_status")),
            "received_status": _clean_status(r.get("received_status")),
        }

    ordered = [_summ(r) for r in pos] + [_summ(r) for r in dns] + [_summ(r) for r in invoices]

    return {
        "order_id": anchor.get("order_id") if anchor else order_id,
        "documents": ordered,
        "po_status": reconciled_po,
        "delivery_stage": delivery["stage"],
        "fulfilment_mode": delivery["mode"],
        "lines": delivery["lines"],
        "unmatched_items": delivery["unmatched"],
        "invoiced": bool(invoices),
        "paid": _is_paid(invoices),
        "counts": {"po": len(pos), "dn": len(dns), "invoice": len(invoices)},
    }


def reconcile_order(order_id: str, user_id: str, evidence_removed: bool = False) -> dict:
    """Reconcile and PERSIST every PO in an order from its Delivery Notes.
    Pass `evidence_removed=True` when a DN was just deleted from, or moved out
    of, this order. Safe to call repeatedly — a no-op when nothing changed."""
    return _reconcile_key(order_key(order_id), user_id, evidence_removed, label=order_id)


def _reconcile_key(key: str, user_id: str, evidence_removed: bool = False, label: str = "") -> dict:
    from dataset_manager import update_record_for_user

    records = _records_for_key(key, user_id)
    pos, dns, _invoices = _classify(records)

    changes: list[dict] = []
    for po in pos:
        new_status = reconcile_po_status(po, dns, evidence_removed=evidence_removed)
        if not new_status:
            continue
        doc_id = po.get("document_id")
        try:
            update_record_for_user(user_id, doc_id, {"po_status": new_status})
            changes.append({
                "document_id": doc_id,
                "from": _norm(po.get("po_status")),
                "to": new_status,
            })
        except Exception as err:  # never let reconciliation break a save
            print(f"[RECONCILE] failed to update {doc_id}: {err}", flush=True)

    return {"order_id": label or key, "changes": changes, "po_count": len(pos), "dn_count": len(dns)}


def reconcile_after_change(
    user_id: str, before: Optional[dict], after: Optional[dict]
) -> list[dict]:
    """Reconcile every order a document change touched.

    `before` / `after` are the document's record before and after the change
    (None for a create / delete). When a DN leaves an order — deleted, or its
    PO reference changed — that order is reconciled with evidence_removed so a
    PO it had fulfilled reverts. Never raises."""
    old_key = link_key(before) if before else ""
    new_key = link_key(after) if after else ""
    was_dn = bool(before) and _norm(before.get("document_type")) == "dn"

    summaries = []
    for key, removed in _touched_orders(old_key, new_key, was_dn):
        try:
            summaries.append(_reconcile_key(key, user_id, evidence_removed=removed))
        except Exception as err:
            print(f"[RECONCILE] order {key} failed (non-fatal): {err}", flush=True)
    return summaries


def _touched_orders(old_key: str, new_key: str, was_dn: bool) -> Iterable[tuple[str, bool]]:
    if old_key and old_key != new_key:
        yield old_key, was_dn
    if new_key:
        yield new_key, False


def reconcile_all_orders(user_id: Optional[str] = None) -> list[dict]:
    """Reconcile every order (optionally for one tenant) — a backfill for data
    saved before reconciliation existed or while it was failing."""
    from dataset_manager import load_all_records

    seen: set[tuple[str, str]] = set()
    summaries = []
    for r in load_all_records(user_id=user_id):
        if _norm(r.get("document_type")) != "po":
            continue
        tenant, key = str(r.get("user_id")), link_key(r)
        if key and (tenant, key) not in seen:
            seen.add((tenant, key))
            summaries.append(_reconcile_key(key, tenant, label=str(r.get("order_id") or key)))
    return summaries
