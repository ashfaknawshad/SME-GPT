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


# ── Linked orders (migration 012): PO -> DN -> Invoice -> Receipt ────────────
# Orders are now the connected components of DocLink chain links
# (document_links.py) instead of documents that happen to share an order_id
# string, and are matched line-by-line by order_matching.compute_order.

def _chain_inputs(user_id: str, records: list[dict] | None = None):
    from dataset_manager import load_all_records
    from document_links import prepare

    records = records if records is not None else load_all_records(user_id=user_id)
    prepared = {
        str(r.get("document_id")): prepare(r)
        for r in records
        if _norm(r.get("document_type")) in ("po", "dn", "invoice", "receipt")
    }
    return records, prepared


def _orders(prepared: dict, links: list[dict]) -> list[tuple[set, list[dict]]]:
    """[(doc ids, active links)] for every connected order with 2+ documents."""
    from document_links import ACTIVE_STATUSES, chain_component

    active = [l for l in links if l["status"] in ACTIVE_STATUSES and l["from"] in prepared and l["to"] in prepared]
    seen: set = set()
    out = []
    for doc_id in prepared:
        if doc_id in seen:
            continue
        comp = chain_component(doc_id, active)
        seen |= comp
        if len(comp) > 1:
            out.append((comp, [l for l in active if l["from"] in comp]))
    return out


def _lost_delivery_updates(prepared: dict, before: list[dict], after: list[dict]) -> list[dict]:
    """Reverts for POs whose last Delivery Note was deleted or unlinked. Such a
    PO has no DNs left for compute_order to judge, and a PO that never had one
    keeps whatever status it was given, so this needs the before/after links."""
    from document_links import ACTIVE_STATUSES
    from order_matching import PO_DELIVERY_STATES, base_po_status

    def with_dns(links: list[dict]) -> set:
        return {l["to"] for l in links if l["link_type"] == "po_dn" and l["status"] in ACTIVE_STATUSES}

    current_links = [l for l in after if l["from"] in prepared]
    updates = []
    for po_id in with_dns(before) - with_dns(current_links):
        po = prepared.get(po_id)
        current = _norm(po["record"].get("po_status")) if po else ""
        if current in PO_DELIVERY_STATES:
            updates.append({"document_id": po_id, "field": "po_status", "from": current,
                            "to": base_po_status(po["record"])})
    return updates


def reconcile_tenant(user_id: str, dry_run: bool = False, records: list[dict] | None = None) -> dict:
    """Relink a tenant's documents, match every linked order, and persist the
    status updates (PO delivery, invoice payment). With dry_run nothing is
    written and the report lists what would change."""
    from dataset_manager import update_record_for_user
    from document_links import load_tenant_links, relink_tenant
    from order_matching import compute_order

    records, prepared = _chain_inputs(user_id, records)
    links_before = load_tenant_links(user_id)
    link_report = relink_tenant(user_id, dry_run=dry_run, records=records)
    links = load_tenant_links(user_id)
    if dry_run:  # DB still holds the old automatic links; use the proposal instead
        links = [l for l in links if l["status"] in ("confirmed", "rejected")] + link_report["links"]

    orders, all_updates = [], []
    for comp, comp_links in _orders(prepared, links):
        view = compute_order([prepared[d] for d in comp], comp_links)
        orders.append({"documents": sorted(comp), "status": view["order_status"],
                       "issues": [i["message_en"] for i in view["issues"]], "updates": view["updates"]})
        all_updates.extend(view["updates"])
    all_updates.extend(_lost_delivery_updates(prepared, links_before, links))

    applied = []
    if not dry_run:
        by_doc: dict[str, dict] = {}
        for u in all_updates:
            by_doc.setdefault(u["document_id"], {})[u["field"]] = u["to"]
        by_id = {str(r.get("document_id")): r for r in records}
        for doc_id, fields in by_doc.items():
            try:
                update_record_for_user(user_id, doc_id, fields)
                applied.append({"document_id": doc_id, **fields})
                by_id.get(doc_id, {}).update(fields)  # keep the in-memory copy current
            except Exception as err:  # never let reconciliation break a save
                print(f"[RECONCILE] failed to update {doc_id}: {err}", flush=True)

    return {"links": link_report, "orders": orders, "updates": all_updates, "applied": applied,
            "records": records, "tenant_links": links}


def _summary(prep: dict) -> dict:
    rec = prep["record"]
    return {
        "document_id": prep["document_id"], "document_type": prep["type"],
        "number": prep["refs"].get("doc_number") or "", "date": rec.get("date"),
        "supplier_name": rec.get("supplier_name") or "", "amount": prep["total"],
        "currency": rec.get("currency") or "LKR",
    }


def build_chain_view(document_id: str, user_id: str, records: list[dict] | None = None,
                     links: list[dict] | None = None) -> Optional[dict]:
    """The linked order around one document, for the document page: every
    document in the chain, line-by-line ordered/delivered/invoiced, invoice
    payments, issues, and pending link suggestions. Read-only.

    Keeps the old build_order_view keys (order_id, documents, po_status,
    delivery_stage, invoiced, paid, counts) so existing UI keeps working.
    Returns None when the document has no links and no suggestions.
    """
    from document_links import chain_component, load_tenant_links
    from order_matching import compute_order

    _records, prepared = _chain_inputs(user_id, records)
    if document_id not in prepared:
        return None
    links = links if links is not None else load_tenant_links(user_id)
    comp = chain_component(document_id, links)
    comp_links = [l for l in links if l["status"] in ("auto", "confirmed") and l["from"] in comp]
    suggestions = [
        dict(l, other=_summary(prepared[l["to"] if l["from"] in comp else l["from"]]))
        for l in links
        if l["status"] == "suggested" and (l["from"] in comp or l["to"] in comp)
        and l["from"] in prepared and l["to"] in prepared
    ]
    if len(comp) < 2 and not suggestions:
        return None

    view = compute_order([prepared[d] for d in comp if d in prepared], comp_links)
    pos = [d for d in view["documents"] if d["document_type"] == "po"]
    po_update = next((u["to"] for u in view["updates"] if u["field"] == "po_status"), None)
    anchor = pos[0] if pos else (view["documents"][0] if view["documents"] else {})
    view.update({
        "order_id": anchor.get("number") or anchor.get("document_id"),
        "po_status": po_update or (_norm(pos[0].get("po_status")) if pos else None) or ("pending" if pos else None),
        "invoiced": view["invoiced_stage"] != "none",
        "paid": view["payment_stage"] == "full",
        "links": comp_links,
        "suggestions": suggestions,
    })
    return view


def tenant_link_summary(user_id: str) -> dict[str, dict]:
    """{document_id: {order, status, issues, linked}} for every linked document
    -- one call for the Files list's chain chips and "Has issues" filter."""
    views, links = tenant_order_views(user_id)
    out: dict[str, dict] = {}
    for v in views:
        warn = [i for i in v["issues"] if i["severity"] in ("warning", "error")]
        for d in v["document_ids"]:
            out[d] = {"order": v["order_id"], "status": v["order_status"], "issues": len(warn),
                      "linked": len(v["document_ids"]) - 1}
    suggested = {l["from"] for l in links if l["status"] == "suggested"} | {l["to"] for l in links if l["status"] == "suggested"}
    for d in suggested:
        out.setdefault(d, {"order": None, "status": None, "issues": 0, "linked": 0})["suggestion"] = True
    return out


def tenant_order_views(user_id: str, records: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Every linked order of a tenant, fully matched. Returns (views, links).
    Each view is order_matching.compute_order() plus order_id (the anchor's
    number), anchor_document_id and document_ids."""
    from document_links import load_tenant_links
    from order_matching import compute_order

    _records, prepared = _chain_inputs(user_id, records)
    links = load_tenant_links(user_id)
    views = []
    for comp, comp_links in _orders(prepared, links):
        view = compute_order([prepared[d] for d in comp], comp_links)
        pos = [d for d in view["documents"] if d["document_type"] == "po"]
        anchor = pos[0] if pos else view["documents"][0]
        view.update({
            "order_id": anchor.get("number") or anchor.get("document_id"),
            "anchor_document_id": anchor.get("document_id"),
            "document_ids": sorted(comp),
        })
        views.append(view)
    return views, links


_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def tenant_order_issues(user_id: str) -> dict:
    """Dashboard "Order issues" card: orders that need attention, worst first,
    plus counts per kind of issue and pending link suggestions."""
    views, links = tenant_order_views(user_id)
    orders = []
    counts = {"partial_delivery": 0, "over_billed": 0, "price_mismatch": 0, "unpaid_balance": 0,
              "over_delivery": 0, "overpaid": 0, "item_not_on_po": 0, "delivered_not_invoiced": 0}
    for v in views:
        for i in v["issues"]:
            counts[i["code"]] = counts.get(i["code"], 0) + 1
        if not v["issues"]:
            continue
        issues = sorted(v["issues"], key=lambda i: _SEVERITY_RANK.get(i["severity"], 3))
        orders.append({
            "order_id": v["order_id"], "anchor_document_id": v["anchor_document_id"],
            "order_status": v["order_status"], "counts": v["counts"], "totals": v["totals"],
            "issues": [{k: i[k] for k in ("code", "severity", "message_en", "message_si")} for i in issues],
        })
    orders.sort(key=lambda o: (_SEVERITY_RANK.get(o["issues"][0]["severity"], 3), -len(o["issues"])))
    suggestions = len({(l["from"], l["to"]) for l in links if l["status"] == "suggested"})
    return {"orders": orders, "counts": counts, "suggestions": suggestions, "linked_orders": len(views)}
