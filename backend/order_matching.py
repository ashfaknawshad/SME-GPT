"""Three-way match + payment for one linked order (PO -> DN -> Invoice -> Receipt).

Pure functions over document_links.prepare() dicts and the active links
between them -- no I/O -- so every rule is unit-testable.

Per line (matched by item description):
    ordered (PO)  vs  delivered (sum of DNs)  vs  invoiced (sum of invoices)
    PO unit price vs  invoice unit price
Per invoice:
    total vs paid (linked receipts, or the invoice marked paid by the user)

Outputs the order's delivery / invoicing / payment stage, a plain-language
issue list (English + Sinhala), and the status updates to persist.

A PO's delivery status follows the current evidence, in both directions: only
Delivery Notes marked delivered count, and when that evidence goes away a
fulfilled / partially delivered PO falls back to approved or pending. Payment
statuses only move forward, and a manual rejected / cancelled PO never changes.
"""
from __future__ import annotations

from document_links import item_similarity

_PO_TERMINAL = {"rejected", "cancelled"}
# The statuses delivery evidence sets, so the only ones it may take back.
PO_DELIVERY_STATES = {"fulfilled", "partially_delivered"}
_LINE_MATCH = 0.5          # description similarity needed to pair lines
_SINGLE_LINE_MATCH = 0.25  # looser when both sides have exactly one line
_CLEAR_WINNER = 0.35       # ...or when one candidate clearly beats the rest
_CLEAR_MARGIN = 0.15


def base_po_status(po_record: dict) -> str:
    """The status a PO falls back to when its delivery evidence goes away."""
    approver = str(po_record.get("approved_by") or "").strip().upper()
    return "approved" if approver not in ("", "NULL", "NONE") else "pending"


def _q(v: float) -> str:
    return f"{v:,.0f}" if abs(v - round(v)) < 1e-9 else f"{v:,.2f}"


def _m(v: float) -> str:
    return f"{v:,.2f}"


def _label(doc: dict) -> str:
    return doc["refs"].get("doc_number") or doc["document_id"]


def _issue(code: str, severity: str, docs: list[str], en: str, si: str, **data) -> dict:
    return {"code": code, "severity": severity, "documents": docs, "message_en": en, "message_si": si, "data": data}


def _assign_lines(lines: list[dict], catalog: list[dict]) -> tuple[dict[int, list[dict]], list[dict]]:
    """Pair each line with its best catalog line. Returns ({catalog idx: [lines]}, unmatched)."""
    matched: dict[int, list[dict]] = {}
    single = len(catalog) == 1 and len(lines) == 1
    floor = _SINGLE_LINE_MATCH if single else _LINE_MATCH
    raw = [[item_similarity(line, c) for c in catalog] for line in lines]

    def acceptable(s: float, li: int) -> bool:
        if s >= floor:
            return True
        # An OCR typo can drag the right match below the floor; accept it when
        # it is still clearly the best candidate for this line.
        others = sorted(raw[li], reverse=True)
        runner_up = others[1] if len(others) > 1 else 0.0
        return s >= _CLEAR_WINNER and s == others[0] and s - runner_up >= _CLEAR_MARGIN

    scores = sorted(
        ((raw[li][ci], li, ci) for li in range(len(lines)) for ci in range(len(catalog))),
        key=lambda t: -t[0],
    )
    # Pass 1: best pairs first, one line per catalog line (a DN usually lists
    # each ordered item once).
    taken_line, taken_cat = set(), set()
    for s, li, ci in scores:
        if not acceptable(s, li):
            continue
        if li in taken_line or ci in taken_cat:
            continue
        matched.setdefault(ci, []).append(lines[li])
        taken_line.add(li)
        taken_cat.add(ci)
    # Pass 2: remaining lines (e.g. the same item listed twice) go to their best match.
    for s, li, ci in scores:
        if not acceptable(s, li):
            continue
        if li in taken_line:
            continue
        matched.setdefault(ci, []).append(lines[li])
        taken_line.add(li)
    unmatched = [line for li, line in enumerate(lines) if li not in taken_line]
    return matched, unmatched


def _flow_side(doc: dict) -> str:
    return "sale" if doc["flow"] in ("receivable", "cash_inflow") else "purchase"


def compute_order(docs: list[dict], links: list[dict]) -> dict:
    """The full match for one connected order. `links` are its active links."""
    by_type = {t: sorted([d for d in docs if d["type"] == t], key=lambda d: (d["date"] is None, d["date"] or 0))
               for t in ("po", "dn", "invoice", "receipt")}
    pos, dns, invoices, receipts = by_type["po"], by_type["dn"], by_type["invoice"], by_type["receipt"]
    currency = next((str(d["record"].get("currency") or "LKR") for d in docs), "LKR")
    issues: list[dict] = []

    # ── lines: ordered / delivered / invoiced ────────────────────────────────
    catalog_src = pos or invoices or dns
    catalog = [dict(it, source=d["document_id"]) for d in catalog_src for it in d["items"] if it["tokens"]]
    lines = [{
        "description": c["description"],
        "ordered": c["qty"] if pos else None,
        "po_unit_price": c["unit_price"] if pos else None,
        "delivered": 0.0, "invoiced": 0.0,
        "invoice_unit_price": None, "_inv_value": 0.0,
    } for c in catalog]

    from order_reconciliation import _delivery_stage, _dn_delivery

    # Only goods marked delivered have arrived: a pending, delayed or returned
    # DN lists what should come, and a partially delivered DN overstates it.
    delivered_dns = [d for d in dns if _dn_delivery(d["record"]) == "delivered"]
    # A partially delivered DN can't say how much arrived, so the order can't be
    # complete while one is outstanding.
    any_partial_dn = any(_dn_delivery(d["record"]) == "partial" for d in dns)

    if catalog:
        for dn in dns:
            matched, extra = _assign_lines(dn["items"], catalog)
            if dn in delivered_dns:
                for i, ls in matched.items():
                    lines[i]["delivered"] += sum(l["qty"] for l in ls)
            if pos:
                for l in extra:
                    issues.append(_issue(
                        "item_not_on_po", "warning", [dn["document_id"]],
                        f'{_label(dn)} includes "{l["description"]}", which is not on the purchase order.',
                        f'{_label(dn)} හි ඇති "{l["description"]}" මිලදී ගැනීමේ ඇණවුමේ නැත.',
                        item=l["description"]))
        for inv in invoices if (pos or dns) else []:
            matched, extra = _assign_lines(inv["items"], catalog)
            for i, ls in matched.items():
                qty = sum(l["qty"] for l in ls)
                lines[i]["invoiced"] += qty
                lines[i]["_inv_value"] += sum(l["qty"] * l["unit_price"] for l in ls)
            if pos:
                for l in extra:
                    issues.append(_issue(
                        "item_not_on_po", "warning", [inv["document_id"]],
                        f'{_label(inv)} includes "{l["description"]}", which is not on the purchase order.',
                        f'{_label(inv)} හි ඇති "{l["description"]}" මිලදී ගැනීමේ ඇණවුමේ නැත.',
                        item=l["description"]))
        if not (pos or dns):  # invoice-only order: the invoice IS the catalog
            for i, c in enumerate(catalog):
                lines[i]["invoiced"] = c["qty"]
                lines[i]["_inv_value"] = c["qty"] * c["unit_price"]

    for ln in lines:
        if ln["invoiced"]:
            ln["invoice_unit_price"] = round(ln["_inv_value"] / ln["invoiced"], 2) if ln["_inv_value"] else None
        ordered, delivered, invoiced = ln["ordered"], ln["delivered"], ln["invoiced"]
        if ordered:
            ln["delivery_status"] = ("none" if delivered <= 0 else "partial" if delivered < ordered
                                     else "full" if delivered == ordered else "over")
        else:
            ln["delivery_status"] = "full" if delivered else "none"
        item = ln["description"]
        po_label = _label(pos[0]) if pos else ""

        if ordered and dns and 0 < delivered < ordered:
            issues.append(_issue(
                "partial_delivery", "info", [pos[0]["document_id"]] + [d["document_id"] for d in dns],
                f"{po_label}: {_q(delivered)} of {_q(ordered)} {item} delivered so far ({_q(ordered - delivered)} still to come).",
                f"{po_label}: {item} {_q(ordered)} න් {_q(delivered)} ක් පමණක් ලැබී ඇත (තව {_q(ordered - delivered)} ක් ලැබිය යුතුයි).",
                item=item, ordered=ordered, delivered=delivered))
        if ordered and delivered > ordered:
            issues.append(_issue(
                "over_delivery", "warning", [d["document_id"] for d in dns],
                f"{item}: {_q(delivered)} delivered but only {_q(ordered)} ordered on {po_label}.",
                f"{item}: {po_label} හි ඇණවුම් කළේ {_q(ordered)} ක් වුවත් {_q(delivered)} ක් ලැබී ඇත.",
                item=item, ordered=ordered, delivered=delivered))
        basis = delivered if dns else ordered
        if invoices and basis is not None and (dns or ordered) and invoiced > (basis or 0) + 1e-9:
            inv_ids = [i["document_id"] for i in invoices]
            what_en = "were delivered" if dns else "were ordered"
            what_si = "ලැබී ඇත" if dns else "ඇණවුම් කර ඇත"
            inv_labels = ", ".join(_label(i) for i in invoices)
            if basis:
                en = f"{inv_labels} bills {_q(invoiced)} {item}, but only {_q(basis)} {what_en}."
                si = f"{inv_labels} මගින් {item} {_q(invoiced)} කට බිල් කර ඇතත්, {_q(basis)} ක් පමණක් {what_si}."
            else:
                en = f"{inv_labels} bills {_q(invoiced)} {item}, but none {what_en}."
                si = f"{inv_labels} මගින් {item} {_q(invoiced)} කට බිල් කර ඇතත්, කිසිවක් {what_si.replace('ඇත', 'නැත')}."
            issues.append(_issue("over_billed", "error", inv_ids, en, si, item=item, invoiced=invoiced, basis=basis))
        po_p, inv_p = ln["po_unit_price"], ln["invoice_unit_price"]
        if po_p and inv_p and abs(inv_p - po_p) > max(0.5, 0.005 * po_p):
            issues.append(_issue(
                "price_mismatch", "warning", [i["document_id"] for i in invoices],
                f"{item}: invoiced at {currency} {_m(inv_p)} each, but the PO price is {currency} {_m(po_p)} "
                f"({'+' if inv_p > po_p else '−'}{currency} {_m(abs(inv_p - po_p))}).",
                f"{item}: ඉන්වොයිසියේ ඒකක මිල {currency} {_m(inv_p)} වන අතර PO මිල {currency} {_m(po_p)} වේ.",
                item=item, po_unit_price=po_p, invoice_unit_price=inv_p))
        ln.pop("_inv_value", None)

    # ── delivery stage ───────────────────────────────────────────────────────
    qty_lines = [l for l in lines if l["ordered"]]
    if not dns:
        delivery_stage = "none"
    elif qty_lines and delivered_dns and all(any(i["qty"] > 0 for i in d["items"]) for d in delivered_dns):
        # Delivered goods that match nothing on the PO stay "partial", so an
        # unrecognised item can never fulfil an order.
        all_lines_met = all(l["delivered"] >= l["ordered"] for l in qty_lines)
        delivery_stage = "full" if all_lines_met and not any_partial_dn else "partial"
    else:
        # No comparable quantities: fall back to the DNs' own delivery status.
        delivery_stage = _delivery_stage([d["record"] for d in dns])

    if dns and not invoices and delivery_stage in ("partial", "full"):
        issues.append(_issue(
            "delivered_not_invoiced", "info", [d["document_id"] for d in dns],
            f"Goods were delivered ({', '.join(_label(d) for d in dns)}) but no invoice is linked yet.",
            f"භාණ්ඩ ලැබී ඇත ({', '.join(_label(d) for d in dns)}) නමුත් තවම ඉන්වොයිසියක් සම්බන්ධ කර නැත.",
        ))

    # ── payment per invoice ──────────────────────────────────────────────────
    receipts_by_inv: dict[str, list[dict]] = {}
    rmap = {r["document_id"]: r for r in receipts}
    for l in links:
        if l["link_type"] == "invoice_receipt" and l["from"] in rmap:
            receipts_by_inv.setdefault(l["to"], []).append(rmap[l["from"]])

    invoice_rows = []
    for inv in invoices:
        rec = inv["record"]
        side = _flow_side(inv)
        paid_flag = (str(rec.get("paid_status") or "").lower() == "paid" if side == "purchase"
                     else str(rec.get("received_status") or "").lower() == "received")
        pays = receipts_by_inv.get(inv["document_id"], [])
        paid_amount = sum(r["total"] for r in pays)
        total = inv["total"]
        if paid_flag and paid_amount < total:
            status, balance = "paid", 0.0  # the user marked it paid (e.g. paid by bank transfer)
        elif total and paid_amount >= total - 1:
            status, balance = "paid", 0.0
        elif paid_amount > 0:
            status, balance = "partially_paid", round(total - paid_amount, 2)
        else:
            status, balance = "unpaid", round(total, 2)
        invoice_rows.append({
            "document_id": inv["document_id"], "number": _label(inv), "total": total,
            "paid": round(paid_amount, 2), "balance": balance, "status": status, "side": side,
            "receipts": [r["document_id"] for r in pays],
        })
        if total and paid_amount > total + 1:
            issues.append(_issue(
                "overpaid", "warning", [inv["document_id"]] + [r["document_id"] for r in pays],
                f"{_label(inv)} was paid {currency} {_m(paid_amount - total)} more than its total.",
                f"{_label(inv)} සඳහා එහි මුළු මුදලට වඩා {currency} {_m(paid_amount - total)} ක් වැඩිපුර ගෙවා ඇත.",
                excess=paid_amount - total))
        elif status == "partially_paid":
            en_verb, si_verb = ("to be paid", "ගෙවිය යුතුයි") if side == "purchase" else ("to be received", "ලැබිය යුතුයි")
            issues.append(_issue(
                "unpaid_balance", "info", [inv["document_id"]],
                f"{_label(inv)}: {currency} {_m(paid_amount)} paid, {currency} {_m(balance)} still {en_verb}.",
                f"{_label(inv)}: {currency} {_m(paid_amount)} ක් ගෙවා ඇත, තව {currency} {_m(balance)} ක් {si_verb}.",
                paid=paid_amount, balance=balance))

    ordered_total = sum(p["total"] for p in pos)
    invoiced_total = sum(i["total"] for i in invoices)
    paid_total = sum(r["paid"] if r["status"] != "paid" else r["total"] for r in invoice_rows)
    invoiced_stage = ("none" if not invoices else
                      "full" if not pos or invoiced_total >= ordered_total - 1 else "partial")
    payment_stage = ("none" if not invoice_rows or paid_total <= 0 else
                     "full" if all(r["status"] == "paid" for r in invoice_rows) else "partial")

    # ── status updates to persist ────────────────────────────────────────────
    # PO delivery status follows its linked DNs both ways: it can also move
    # back down (fulfilled -> partially_delivered -> approved/pending) when a
    # DN is un-marked, removed, or was wrongly counted before. Manual terminal
    # states (rejected/cancelled) are never touched.
    updates: list[dict] = []
    for po in pos:
        current = str(po["record"].get("po_status") or "").lower()
        target = {"full": "fulfilled", "partial": "partially_delivered"}.get(delivery_stage)
        if target is None and dns and current in PO_DELIVERY_STATES:
            target = base_po_status(po["record"])
        if target and current not in _PO_TERMINAL and current != target:
            updates.append({"document_id": po["document_id"], "field": "po_status", "from": current, "to": target})
    for row, inv in zip(invoice_rows, invoices):
        rec = inv["record"]
        cur_inv = str(rec.get("invoice_status") or "").lower()
        if row["status"] in ("paid", "partially_paid") and cur_inv != row["status"] and cur_inv != "paid":
            updates.append({"document_id": inv["document_id"], "field": "invoice_status", "from": cur_inv, "to": row["status"]})
        if row["receipts"]:  # only receipts (evidence) move the money fields
            field = "paid_status" if row["side"] == "purchase" else "received_status"
            done = "paid" if row["side"] == "purchase" else "received"
            cur = str(rec.get(field) or "").lower()
            target = done if row["status"] == "paid" else "partial" if row["status"] == "partially_paid" else None
            if target and cur != done and cur != target:
                updates.append({"document_id": inv["document_id"], "field": field, "from": cur, "to": target})

    # One summary chip; the three *_stage fields carry the detail.
    if payment_stage == "full":
        order_status = "paid"
    elif payment_stage == "partial":
        order_status = "partially_paid"
    elif invoices:
        order_status = "invoiced"
    elif delivery_stage == "full":
        order_status = "delivered"
    elif delivery_stage == "partial":
        order_status = "partially_delivered"
    else:
        order_status = "ordered" if pos else "open"

    return {
        "documents": [{
            "document_id": d["document_id"], "document_type": d["type"], "number": d["refs"].get("doc_number") or "",
            "date": d["record"].get("date"), "supplier_name": d["record"].get("supplier_name") or "",
            "amount": d["total"], "currency": d["record"].get("currency") or currency,
            "po_status": d["record"].get("po_status"), "dn_status": d["record"].get("dn_status"),
            "invoice_status": d["record"].get("invoice_status"), "paid_status": d["record"].get("paid_status"),
            "received_status": d["record"].get("received_status"),
        } for d in pos + dns + invoices + receipts],
        "lines": lines,
        "invoices": invoice_rows,
        "delivery_stage": delivery_stage,
        "invoiced_stage": invoiced_stage,
        "payment_stage": payment_stage,
        "order_status": order_status,
        "totals": {
            "ordered": round(ordered_total, 2), "invoiced": round(invoiced_total, 2),
            "paid": round(paid_total, 2), "balance_due": round(sum(r["balance"] for r in invoice_rows), 2),
            "currency": currency,
        },
        "counts": {"po": len(pos), "dn": len(dns), "invoice": len(invoices), "receipt": len(receipts)},
        "issues": issues,
        "updates": updates,
    }
