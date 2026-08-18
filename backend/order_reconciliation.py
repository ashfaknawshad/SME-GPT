"""Order reconciliation — the PO → DN → Invoice state machine.

Until now each document carried a status derived only from its OWN fields
(correction_engine._derive_workflow_status). This module adds the missing
cross-document layer: documents that share an `order_id` form one *order*, and
a Purchase Order's delivery status is reconciled from the Delivery Notes booked
against it.

Design decisions (locked with the product owner):
  * FULFILLED = fully DELIVERED. A PO becomes `fulfilled` once every linked
    Delivery Note is `delivered`; `partially_delivered` once any DN is
    delivered/partial but not all. Payment is tracked separately on the invoice
    and does NOT gate PO fulfilment.
  * PERSIST. The reconciled `po_status` is written back to the PO so repository
    chips, reports, and chat all agree. Manual terminal states (`rejected`,
    `cancelled`) are never overwritten.

The rule function `reconcile_po_status` is pure and deterministic (no I/O) so it
is fully unit-testable; `reconcile_order` / `build_order_view` wrap it with
tenant-scoped DB reads and the single persisted write.
"""
from __future__ import annotations

from typing import Optional

# Manually-set terminal PO states the cascade must never clobber.
_PO_TERMINAL = {"rejected", "cancelled"}

# A DN's delivery is expressed two ways depending on how it was set: the
# extraction pipeline derives `dn_status` (delivered / partially_delivered),
# while the analysis-page Edit form (the only user-facing control) sets the DN's
# `received_status` to "delivered"/"not_delivered" and does NOT re-derive
# dn_status. Reconciliation therefore has to read BOTH so a user marking a DN
# delivered actually cascades to its PO.
_DELIVERED_TOKENS = {"delivered", "received"}
_PARTIAL_TOKENS = {"partially_delivered", "partial", "partially_received"}


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _dn_delivery(dn: dict) -> str:
    """A Delivery Note's progress: 'delivered' | 'partial' | 'none'.
    Reads dn_status first (pipeline-derived) then falls back to received_status
    (what the Edit form sets)."""
    for field in ("dn_status", "received_status"):
        v = _norm(dn.get(field))
        if v in _DELIVERED_TOKENS:
            return "delivered"
        if v in _PARTIAL_TOKENS:
            return "partial"
    return "none"


def reconcile_po_status(po: dict, delivery_notes: list[dict]) -> Optional[str]:
    """Return the reconciled `po_status` for a PO given the Delivery Notes in
    its order, or None when nothing should change.

    Pure function — `po` and `delivery_notes` are plain record dicts. Rule:
      * PO in a terminal manual state (rejected/cancelled) -> None (untouched).
      * No delivery notes -> None (nothing to reconcile from; the PO keeps its
        own self-derived pending/approved status).
      * Every DN delivered -> "fulfilled".
      * Some (but not all) DNs delivered/partial -> "partially_delivered".
      * DNs exist but none delivered yet -> None (still pending/approved).
    A result equal to the PO's current status also returns None (no-op).
    """
    current = _norm(po.get("po_status"))
    if current in _PO_TERMINAL:
        return None
    if not delivery_notes:
        return None

    progress = [_dn_delivery(dn) for dn in delivery_notes]
    delivered = sum(1 for p in progress if p == "delivered")
    partial = sum(1 for p in progress if p == "partial")

    if delivered == len(progress):
        new_status = "fulfilled"
    elif delivered or partial:
        new_status = "partially_delivered"
    else:
        return None  # DNs booked but none delivered — leave the PO as-is

    return new_status if new_status != current else None


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


def _order_records(order_id: str, user_id: str) -> list[dict]:
    """All of the tenant's records sharing this order_id (case-insensitive)."""
    from dataset_manager import load_all_records

    target = _norm(order_id)
    if not target or target in ("null", "none"):
        return []
    return [r for r in load_all_records(user_id=user_id) if _norm(r.get("order_id")) == target]


def _delivery_stage(dns: list[dict]) -> str:
    """'none' | 'partial' | 'full' — the order's overall delivery progress."""
    if not dns:
        return "none"
    progress = [_dn_delivery(dn) for dn in dns]
    delivered = sum(1 for p in progress if p == "delivered")
    partial = sum(1 for p in progress if p == "partial")
    if delivered == len(progress):
        return "full"
    if delivered or partial:
        return "partial"
    return "none"


def _is_paid(invoices: list[dict]) -> bool:
    """True when at least one linked invoice is fully paid."""
    return any(_norm(inv.get("paid_status")) == "paid" for inv in invoices)


def build_order_view(order_id: str, user_id: str) -> Optional[dict]:
    """Read-only snapshot of an order: its documents (PO first, then DNs, then
    invoices) plus the reconciled PO status and delivery/invoice/payment flags.
    Returns None if the order has no documents. Does NOT write."""
    records = _order_records(order_id, user_id)
    if not records:
        return None

    pos, dns, invoices = _classify(records)

    # Reconciled PO status (computed, may differ from what's stored until the
    # next save persists it). Use the first PO as the order's anchor.
    reconciled_po = None
    if pos:
        proposed = reconcile_po_status(pos[0], dns)
        reconciled_po = proposed or _norm(pos[0].get("po_status")) or "pending"

    def _summ(r: dict) -> dict:
        return {
            "document_id": r.get("document_id"),
            "document_type": _norm(r.get("document_type")),
            "date": r.get("date"),
            "supplier_name": r.get("supplier_name") or r.get("company_name") or "",
            "amount": r.get("final_total_amount") or r.get("payable_amount") or "",
            "currency": r.get("currency") or "LKR",
            "po_status": r.get("po_status"),
            "dn_status": r.get("dn_status"),
            "invoice_status": r.get("invoice_status"),
            "paid_status": r.get("paid_status"),
            "received_status": r.get("received_status"),
        }

    ordered = [_summ(r) for r in pos] + [_summ(r) for r in dns] + [_summ(r) for r in invoices]

    return {
        "order_id": order_id,
        "documents": ordered,
        "po_status": reconciled_po,
        "delivery_stage": _delivery_stage(dns),
        "invoiced": bool(invoices),
        "paid": _is_paid(invoices),
        "counts": {"po": len(pos), "dn": len(dns), "invoice": len(invoices)},
    }


def reconcile_order(order_id: str, user_id: str) -> dict:
    """Reconcile and PERSIST every PO in an order from its Delivery Notes.
    Called from the save/edit paths (background). Returns a summary of what
    changed. Safe to call repeatedly — it is a no-op when nothing changed."""
    from dataset_manager import update_record_for_user

    records = _order_records(order_id, user_id)
    pos, dns, _invoices = _classify(records)

    changes: list[dict] = []
    for po in pos:
        new_status = reconcile_po_status(po, dns)
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

    return {"order_id": order_id, "changes": changes, "po_count": len(pos), "dn_count": len(dns)}
