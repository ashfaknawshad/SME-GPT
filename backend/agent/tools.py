"""Deterministic tools for the agentic query engine (Phase 1).

The LLM only ever *plans* by calling these tools; every number returned comes
from pal_executor's pandas arithmetic (aggregate_financials) or a direct,
unmodified row lookup (search_documents / get_document_status) -- never from
the LLM itself. This generalizes PAL's single rigid plan-execute cycle
(pal_planner -> pal_validator -> pal_executor -> pal_answer) into a set of
reusable, conversational tool calls while keeping the same safety boundary:
pal_validator.validate_plan() is still the authoritative gate before any
aggregation runs (docs/components/component-3.md "Why").

Tenant isolation (CLAUDE.md "Architecture Invariants"): user_id and
company_name are bound into these closures by build_tools() -- they are never
arguments the LLM can supply, so a prompt-injected or hallucinated user_id can
never leak another tenant's data.
"""
from __future__ import annotations

import copy
from typing import Optional

from langchain_core.tools import tool

import data_tools as dt
from pal_executor import execute_plan
from pal_scope import build_row_records, resolve_scope_with_c4, resolve_scope_with_rag, retrieve_rag_chunks
from pal_validator import validate_plan

_AGG_TO_TASK = {"sum": "aggregate_sum", "avg": "aggregate_avg", "count": "aggregate_count"}


def build_tools(user_id: str, company_name: str) -> tuple[list, list[dict]]:
    """Returns (tools, evidence). `evidence` is a shared list the tools append
    to as they run (out-of-band from what the LLM sees) so the /chat endpoint
    can return the same EvidenceItem shape /ask-query already returns to the
    frontend. Call this once per request/thread -- the closures are not safe
    to share across users."""
    evidence: list[dict] = []

    # One chat turn often calls several tools (and the same tool twice); the
    # tenant's documents can't change mid-turn, so resolve the scope once and
    # hand each tool its own copy. build_tools() runs once per request, so
    # this cache never outlives the turn.
    _scope: dict = {}

    def scope_c4():
        if "c4" not in _scope:
            _scope["c4"] = resolve_scope_with_c4(company_name, user_id)
        df, err = _scope["c4"]
        return df.copy(), err

    @tool
    def aggregate_financials(
        measure_field: str,
        agg: str,
        filters: Optional[list[dict]] = None,
        group_by: Optional[list[str]] = None,
        factor: Optional[float] = None,
    ) -> dict:
        """Compute a deterministic financial total, average, count, max, or min over
        the user's saved financial documents (invoices, receipts, purchase orders,
        delivery notes) for the current company. This tool NEVER hallucinates a
        number -- it runs real pandas arithmetic. Use it for any "how much" /
        "how many" / "total" / "average" question; never answer those from memory.

        Args:
          measure_field: canonical field to aggregate. Use "total" for money
            amounts (almost always what "how much" questions want). Other
            options: qty, unit_price, tax, discount.
          agg: "sum" (total money), "avg" (average), "count" (how many),
            "max", "min".
          filters: list of {"field": ..., "op": ..., "value": ...} objects to
            narrow the rows. Canonical fields: item, description, qty,
            unit_price, total, tax, discount, currency, doc_date, vendor,
            flow_type, paid_status, received_status. Ops: eq, in, contains,
            gte, lte, between (there is no "not equal" -- express exclusion
            with "in" over the values you DO want).

            flow_type values: payable (we owe), receivable (owed to us),
            cash_inflow (money already received), cash_outflow (money already
            paid out) -- for income/revenue use flow_type in [receivable,
            cash_inflow]; for expense/spending HISTORY (money that has
            already moved) use flow_type in [payable, cash_outflow].

            "How much do we STILL owe" / outstanding payables (NOT the same as
            spending history -- cash_outflow is already-paid money and must be
            excluded): filter flow_type eq "payable" AND paid_status in
            ["not_paid", "partial"]. Without the paid_status filter you will
            overcount by including invoices/POs that have already been paid.

            "How much are we STILL owed" / outstanding receivables: filter
            flow_type eq "receivable" AND received_status in
            ["not_received", "partial"]. Without it you will overcount by
            including amounts already received.

            For dates use {"field":"doc_date","op":"between",
            "value":["YYYY-MM-DD","YYYY-MM-DD"]} -- resolve relative periods
            ("this month", "last month", "this year") against today's date
            yourself before calling.
          group_by: optional list of canonical fields (e.g. ["vendor"]) to
            break the total down by group instead of a single number.
          factor: optional scalar multiplier applied to the result AFTER the
            aggregation, done deterministically by this tool. Use it for
            questions that scale a total -- "× 1.15", "add 15% tax/markup"
            (factor 1.15), "after a 10% discount" (factor 0.9). NEVER multiply
            or scale a figure yourself; pass `factor` and let this tool do it.

        Returns a dict with `value`, `currency`, `row_count`, and (if group_by
        was used) `groups`. When `factor` is applied, `value`/`per_currency`
        are the scaled results and `base_value`/`factor_applied` show the
        pre-scaled total and the factor used. If the arguments are invalid you
        get back an `error` explaining why -- fix the arguments and call again.
        """
        df, err = scope_c4()
        if err or df.empty:
            return {"error": err or "No documents found for this company."}

        rows = build_row_records(df)
        group_by = group_by or []
        task = "group_by_sum" if group_by else _AGG_TO_TASK.get(agg, "aggregate_sum")

        plan = {
            "task": task,
            "filters": filters or [],
            "measure": {"field": measure_field, "agg": agg},
            "group_by": group_by,
        }
        is_valid, reason = validate_plan(plan)
        if not is_valid:
            return {"error": f"Invalid arguments: {reason}. Use only canonical fields/ops."}

        computed = execute_plan(plan, rows)

        # Optional deterministic scalar factor (e.g. ×1.15 for "add 15% markup").
        # Done here in Python, NOT by the LLM — this is what lets the agent
        # answer "total × 1.15" while keeping every figure tool-verified.
        applied_factor = None
        base_value = None
        base_per_currency = None
        if factor is not None:
            try:
                f = float(factor)
            except (TypeError, ValueError):
                f = None
            if f is not None and f != 1.0:
                applied_factor = f
                base_value = computed.get("value")
                base_per_currency = copy.deepcopy(computed.get("per_currency"))
                if isinstance(computed.get("value"), (int, float)):
                    computed["value"] = round(computed["value"] * f, 2)
                for entry in (computed.get("per_currency") or []):
                    if isinstance(entry.get("value"), (int, float)):
                        entry["value"] = round(entry["value"] * f, 2)
                for g in (computed.get("groups") or []):
                    if isinstance(g.get("total"), (int, float)):
                        g["total"] = round(g["total"] * f, 2)

        used_doc_ids = {r["document_id"] for r in computed.get("rows_used", [])}
        if used_doc_ids:
            evidence_df = df[df["document_id"].astype(str).isin(used_doc_ids)]
            evidence.extend(dt.build_evidence(
                evidence_df, f"Included by aggregate_financials({task}) over canonical fields."
            ))

        # Trim rows_used from what the LLM sees -- it only needs the computed
        # number(s), not every matched row; full evidence goes to the caller
        # separately via the `evidence` list above.
        result = {
            "value": computed.get("value"),
            "currency": computed.get("currency"),
            "row_count": computed.get("row_count"),
            "operation": computed.get("operation"),
            "groups": computed.get("groups"),
            "per_currency": computed.get("per_currency"),
            "difference": computed.get("difference"),
        }
        if applied_factor is not None:
            # Surface both the base and the scaled figure so the answer can cite
            # either ("total is X; with 15% that's Y") and stay grounded.
            result["factor_applied"] = applied_factor
            result["base_value"] = base_value
            result["base_per_currency"] = base_per_currency
        return result

    @tool
    def search_documents(
        query: str = "",
        document_type: Optional[str] = None,
        limit: int = 10,
    ) -> dict:
        """Find and list specific financial documents for the current company (e.g.
        "unpaid invoices from Virtusa", "delivery notes this month"). Use this when
        the user wants to SEE/LIST documents -- use aggregate_financials instead for
        totals/counts.

        Args:
          query: free-text description of what to find (used for semantic and
            keyword matching against the document set).
          document_type: optional filter -- one of invoice, receipt, po, dn.
          limit: max documents to return (default 10, capped at 50).

        Returns a dict with `documents` (list of matches: id, type, date,
        supplier, amount, currency, status fields) and `count`.
        """
        df, err = resolve_scope_with_rag(query, company_name, user_id, sql_scope_fn=scope_c4)
        if err or df.empty:
            return {"documents": [], "count": 0, "note": err or "No documents found."}

        if document_type:
            df = df[df["document_type"].astype(str).str.lower() == document_type.strip().lower()]
            if df.empty:
                return {"documents": [], "count": 0, "note": f"No {document_type} documents found."}

        matched = df.head(max(1, min(limit, 50)))
        ev = dt.build_evidence(matched, f"Matched search_documents(query={query!r}).")
        evidence.extend(ev)

        summary = [
            {
                "document_id": e["document_id"],
                "document_type": e["document_type"],
                "date": e["date"],
                "supplier_name": e["supplier_name"],
                "amount": e["amount_used"],
                "currency": e["currency"],
                "po_status": e["po_status"],
                "dn_status": e["dn_status"],
                "invoice_status": e["invoice_status"],
            }
            for e in ev
        ]

        # Chunk-level provenance (FR-17): which exact spatial chunks matched, with
        # page/bbox, so the answer can cite "matched on this line" not just the doc.
        matched_ids = {e["document_id"] for e in ev}
        snippets = [
            {
                "document_id": c.get("document_id"),
                "chunk_type": c.get("chunk_type"),
                "page": c.get("page"),
                "bbox": c.get("bbox"),
                "text": (c.get("text") or "")[:160],
            }
            for c in retrieve_rag_chunks(query, user_id, k=6)
            if c.get("document_id") in matched_ids
        ]
        result = {"documents": summary, "count": len(summary), "total_matches": int(len(df))}
        if snippets:
            result["matched_snippets"] = snippets
        return result

    @tool
    def get_document_status(document_id: str) -> dict:
        """Look up ONE specific document by its ID (e.g. "INV-2045", "PO10045") and
        return its full status and details for the current company.

        Args:
          document_id: the document ID to look up (case-insensitive).
        """
        df, err = scope_c4()
        if err or df.empty:
            return {"error": err or "No documents found for this company."}

        match = df[df["document_id"].astype(str).str.lower() == document_id.strip().lower()]
        if match.empty:
            return {"error": f"No document found with ID '{document_id}'."}

        ev = dt.build_evidence(match, f"Direct lookup of document_id={document_id!r}.")
        evidence.extend(ev)
        return ev[0]

    # ── linked orders (PO -> DN -> Invoice -> Receipt; document_links.py) ──
    _orders_cache: dict = {}

    def _order_views():
        """All linked orders for this tenant, computed once per turn."""
        if "views" not in _orders_cache:
            try:
                from order_reconciliation import tenant_order_views
                _orders_cache["views"] = tenant_order_views(user_id)[0]
            except Exception:
                _orders_cache["views"] = []  # degrade: linking unavailable
        return _orders_cache["views"]

    def _chain_evidence(doc_ids, why: str) -> None:
        df, err = scope_c4()
        if err or df.empty:
            return
        sub = df[df["document_id"].astype(str).isin(set(doc_ids))]
        if not sub.empty:
            evidence.extend(dt.build_evidence(sub, why))

    def _compact(view: dict) -> dict:
        return {
            "order": view["order_id"],
            "status": view["order_status"],
            "delivery": view["delivery_stage"],
            "invoicing": view["invoiced_stage"],
            "payment": view["payment_stage"],
            "documents": [{"id": d["document_id"], "type": d["document_type"], "number": d.get("number") or "",
                           "date": d.get("date"), "amount": d.get("amount")} for d in view["documents"]],
            "lines": [{"item": l["description"], "ordered": l["ordered"], "delivered": l["delivered"],
                       "invoiced": l["invoiced"], "po_unit_price": l["po_unit_price"],
                       "invoice_unit_price": l["invoice_unit_price"], "delivery_status": l["delivery_status"]}
                      for l in view["lines"]],
            "invoices": [{"id": i["document_id"], "number": i["number"], "total": i["total"], "paid": i["paid"],
                          "balance": i["balance"], "status": i["status"]} for i in view["invoices"]],
            "totals": view["totals"],
            "issues": [i["message_en"] for i in view["issues"]],
        }

    @tool
    def get_order_chain(reference: str) -> dict:
        """Look up the linked order -- purchase order -> delivery note(s) ->
        invoice(s) -> receipt(s) -- that a document belongs to: line-by-line
        ordered / delivered / invoiced quantities, PO vs invoice unit prices,
        what each invoice has been paid and still owes, and any issues
        (partial delivery, over-billing, price differences, unpaid balances).
        Use for questions like "has PO8 been fully delivered?", "how many bags
        are still to come?", "was the invoice for PO-2026-041 paid?", "what's
        left to pay on this order?".

        Args:
          reference: a document ID (e.g. "PO8", "IN21") or any printed reference
            number (e.g. "PO-2026-041", "INV-778") -- formatting is ignored.
        """
        from order_reconciliation import _chain_inputs, build_chain_view
        from references import ref_key

        records, prepared = _chain_inputs(user_id)
        ref = reference.strip()
        target = next((d for d in prepared if d.lower() == ref.lower()), None)
        if not target:
            key = ref_key(ref)
            if key:
                order = {"po": 0, "invoice": 1, "dn": 2, "receipt": 3}
                hits = [p for p in prepared.values()
                        if key in (p["own_key"], p["order_key"], ref_key(p["refs"].get("po_ref")))]
                hits.sort(key=lambda p: order.get(p["type"], 9))
                target = hits[0]["document_id"] if hits else None
        if not target:
            return {"error": f"No document or reference number matching '{reference}'."}

        view = build_chain_view(target, user_id, records=records)
        if not view or len(view["documents"]) < 2:
            return {"document": target, "linked": False,
                    "note": "This document is not linked to a purchase order, delivery note, invoice or receipt yet."}
        _chain_evidence([d["document_id"] for d in view["documents"]], f"Linked order {view['order_id']}.")
        out = _compact(view)
        out["pending_link_suggestions"] = len(view.get("suggestions") or [])
        return out

    @tool
    def list_order_issues(kind: Optional[str] = None) -> dict:
        """List the linked orders (PO -> delivery -> invoice -> receipt) that
        need attention, across the whole business. Use for questions like "are
        any deliveries incomplete?", "is any supplier over-billing me?", "which
        invoices still have a balance?", "anything delivered but not invoiced?".

        Args:
          kind: optional filter -- one of partial_delivery, over_delivery,
            over_billed, price_mismatch, item_not_on_po, unpaid_balance,
            overpaid, delivered_not_invoiced. Omit for every kind.
        """
        views = _order_views()
        found = []
        for v in views:
            issues = [i for i in v["issues"] if not kind or i["code"] == kind]
            if not issues:
                continue
            found.append({
                "order": v["order_id"], "status": v["order_status"],
                "documents": [{"id": d["document_id"], "type": d["document_type"], "number": d.get("number") or ""}
                              for d in v["documents"]],
                "issues": [{"kind": i["code"], "message": i["message_en"], **i.get("data", {})} for i in issues],
                "totals": v["totals"],
            })
            _chain_evidence(v["document_ids"], f"Order {v['order_id']} needs attention.")
        return {"orders_checked": len(views), "orders_with_issues": found}

    @tool
    def find_discrepancies(order_id: Optional[str] = None) -> dict:
        """Compare line-item prices between an invoice and the purchase order it
        belongs to, to detect billing discrepancies (invoice charging more/less
        than the PO agreed). Use ONLY when the user asks about discrepancies,
        mismatches, overcharging, or invoice-vs-PO differences -- never
        volunteer this on unrelated questions.

        Args:
          order_id: optional order / PO number (e.g. "PO-2026-501") or document
            ID. When omitted, every order that has both an invoice and a PO is
            checked.

        Returns {"discrepancies": [...], "orders_checked": N}. Each discrepancy
        has description, invoice_price, po_price, diff_pct, and is_discrepancy
        (True when the prices meaningfully differ).
        """
        from references import ref_key

        all_found: list[dict] = []
        orders_checked = 0
        covered: set[str] = set()
        want = ref_key(order_id) if order_id else ""

        # 1) Linked orders (the reliable source: documents are connected by
        #    reference numbers / counterparty / items, not just a shared string).
        for v in _order_views():
            if not (v["counts"]["po"] and v["counts"]["invoice"]):
                continue
            if order_id:
                keys = {ref_key(v["order_id"])} | {ref_key(d.get("number")) for d in v["documents"]}
                ids = {d["document_id"].lower() for d in v["documents"]}
                if want not in keys and order_id.strip().lower() not in ids:
                    continue
            orders_checked += 1
            covered |= set(v["document_ids"])
            inv_numbers = ", ".join(d.get("number") or d["document_id"] for d in v["documents"] if d["document_type"] == "invoice")
            hit = False
            for line in v["lines"]:
                po_p, inv_p = line["po_unit_price"], line["invoice_unit_price"]
                if not po_p or not inv_p:
                    continue
                diff_pct = round((inv_p - po_p) / po_p * 100, 2)
                is_disc = abs(inv_p - po_p) > max(0.5, 0.005 * po_p)
                hit = hit or is_disc
                all_found.append({"order_id": v["order_id"], "invoice": inv_numbers, "description": line["description"],
                                  "invoice_price": inv_p, "po_price": po_p, "diff_pct": diff_pct,
                                  "is_discrepancy": is_disc})
            if hit:
                _chain_evidence(v["document_ids"], f"Invoice-vs-PO price check for order {v['order_id']}.")

        # 2) Legacy: documents sharing an order_id string that aren't linked yet.
        df, err = scope_c4()
        if err or df.empty:
            if orders_checked:
                return {"discrepancies": all_found, "orders_checked": orders_checked}
            return {"error": err or "No documents found for this company."}

        if order_id:
            df = df[df["order_id"].astype(str).str.lower() == order_id.strip().lower()]
            if df.empty and not orders_checked:
                return {"error": f"No documents found with order ID '{order_id}'."}

        # A discrepancy is only meaningful between an invoice and the PO for the
        # SAME order -- comparing across unrelated orders would manufacture false
        # positives.
        for oid, group in df.groupby(df["order_id"].astype(str)):
            if not oid or oid.upper() in ("", "NULL", "NONE"):
                continue
            if set(group["document_id"].astype(str)) <= covered:
                continue  # already checked as a linked order
            types = set(group["document_type"].astype(str).str.lower())
            if not ({"invoice", "po"} <= types):
                continue
            orders_checked += 1
            ev = dt.build_evidence(group, f"Invoice-vs-PO price check for order {oid}.")
            found = dt.detect_discrepancies_in_evidence(ev)
            if any(d.get("is_discrepancy") for d in found):
                evidence.extend(ev)
            for d in found:
                d["order_id"] = oid
            all_found.extend(found)

        return {"discrepancies": all_found, "orders_checked": orders_checked}

    return [aggregate_financials, search_documents, get_document_status, get_order_chain,
            list_order_issues, find_discrepancies], evidence
