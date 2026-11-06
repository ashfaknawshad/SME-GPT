"""Deterministic profit & loss over a tenant's saved documents (no LLM arithmetic).

Each document counts at its final total: what was actually billed or paid, after
discounts and including VAT. VAT is also reported separately, because for a
VAT-registered business it is collected for the government rather than earned,
so profit_excluding_tax is shown alongside the headline figure.

Purchase orders are commitments, not expenses, and delivery notes carry no
amounts, so neither counts. A receipt that settles an invoice already counted is
passed in via exclude_ids, so the same purchase isn't counted twice.
"""
from __future__ import annotations

import pandas as pd

import data_tools as dt
from correction_engine import _parse_date_flexible

_REVENUE_FLOWS = {"receivable", "cash_inflow"}
_EXPENSE_FLOWS = {"payable", "cash_outflow"}
_NON_FINANCIAL_TYPES = {"po", "dn"}

BASIS = (
    "Revenue is receivable invoices and money received; expenses are payable invoices and "
    "money paid. Every amount is the document's final total, after discounts and including "
    "VAT. Purchase orders and delivery notes are not counted."
)


def _new_bucket(currency: str) -> dict:
    return {
        "currency": currency, "revenue": 0.0, "expenses": 0.0,
        "tax_on_revenue": 0.0, "tax_on_expenses": 0.0,
        "discounts_given": 0.0, "discounts_received": 0.0,
        "revenue_documents": 0, "expense_documents": 0,
    }


def _finish_bucket(bucket: dict) -> dict:
    money = ("revenue", "expenses", "tax_on_revenue", "tax_on_expenses", "discounts_given", "discounts_received")
    for key in money:
        bucket[key] = round(bucket[key], 2)
    bucket["profit"] = round(bucket["revenue"] - bucket["expenses"], 2)
    bucket["profit_excluding_tax"] = round(
        (bucket["revenue"] - bucket["tax_on_revenue"]) - (bucket["expenses"] - bucket["tax_on_expenses"]), 2
    )
    bucket["margin_pct"] = round(bucket["profit"] / bucket["revenue"] * 100, 2) if bucket["revenue"] else None
    return bucket


def _in_period(doc_date, start, end) -> bool:
    if start is None and end is None:
        return True
    parsed = _parse_date_flexible(str(doc_date)) if doc_date not in (None, "", "NULL") else None
    if parsed is None:
        return False
    return (start is None or parsed >= start) and (end is None or parsed <= end)


def compute_profit_summary(
    documents_df: pd.DataFrame,
    date_from: str | None = None,
    date_to: str | None = None,
    exclude_ids: set[str] | None = None,
) -> dict:
    start = _parse_date_flexible(date_from) if date_from else None
    end = _parse_date_flexible(date_to) if date_to else None
    excluded = exclude_ids or set()

    buckets: dict[str, dict] = {}
    used_ids: list[str] = []
    skipped = {"purchase_orders_and_delivery_notes": 0, "receipts_settling_a_counted_invoice": 0,
               "unclassified": 0, "outside_period": 0}

    for _, doc in documents_df.iterrows():
        doc_id = str(doc.get("document_id"))
        if str(doc.get("document_type") or "").strip().lower() in _NON_FINANCIAL_TYPES:
            skipped["purchase_orders_and_delivery_notes"] += 1
            continue
        if doc_id in excluded:
            skipped["receipts_settling_a_counted_invoice"] += 1
            continue
        if not _in_period(doc.get("date"), start, end):
            skipped["outside_period"] += 1
            continue

        flow = dt.normalize_flow(doc.get("effective_flow_type") or doc.get("flow_type"))
        if flow not in _REVENUE_FLOWS | _EXPENSE_FLOWS:
            skipped["unclassified"] += 1
            continue

        currency = str(doc.get("currency") or "").strip()
        if currency.upper() in ("", "NULL", "NONE"):
            currency = "LKR"
        bucket = buckets.setdefault(currency, _new_bucket(currency))

        amount = dt.document_total(doc)
        tax = dt.to_float(doc.get("tax_amount"))
        discount = dt.to_float(doc.get("discount_amount"))
        if flow in _REVENUE_FLOWS:
            bucket["revenue"] += amount
            bucket["tax_on_revenue"] += tax
            bucket["discounts_given"] += discount
            bucket["revenue_documents"] += 1
        else:
            bucket["expenses"] += amount
            bucket["tax_on_expenses"] += tax
            bucket["discounts_received"] += discount
            bucket["expense_documents"] += 1
        used_ids.append(doc_id)

    per_currency = [_finish_bucket(b) for _, b in sorted(buckets.items())]

    caveats = []
    if not per_currency:
        caveats.append("No invoices or receipts were found for this period, so there is nothing to calculate yet.")
    elif not any(b["revenue_documents"] for b in per_currency):
        caveats.append("Only expenses are recorded so far: no sales invoices or money received, so this shows as a loss.")
    elif not any(b["expense_documents"] for b in per_currency):
        caveats.append("Only revenue is recorded so far: no expense documents, so this profit is overstated.")
    if len(per_currency) > 1:
        caveats.append("Documents use more than one currency, so each currency is reported separately and never added together.")

    result: dict = {
        "per_currency": per_currency,
        "documents_used": used_ids,
        "documents_skipped": skipped,
        "basis": BASIS,
        "caveats": caveats,
    }
    if len(per_currency) == 1:
        result.update({k: per_currency[0][k] for k in (
            "currency", "revenue", "expenses", "profit", "profit_excluding_tax", "margin_pct",
        )})
    return result
