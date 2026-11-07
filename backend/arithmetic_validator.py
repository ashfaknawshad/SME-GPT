from typing import Any, Dict, List


def to_float(value: Any):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return None

    cleaned = (
        text.replace(",", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .replace("LKR", "")
        .replace("$", "")
        .strip()
    )

    if not cleaned:
        return None

    try:
        return float(cleaned)
    except Exception:
        return None


def round2(value):
    if value is None:
        return None
    return round(float(value), 2)


def values_match(a, b, tolerance=0.05):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tolerance


def _nonzero(value):
    return None if value is None or value == 0 else value


def compute_items_total(items: List[dict]) -> Dict[str, Any]:
    computed_total = 0.0
    printed_line_total = 0.0
    valid_line_count = 0
    used_rows = []

    if not isinstance(items, list):
        return {
            "computed_total": None,
            "printed_lines_total": None,
            "valid_line_count": 0,
            "used_rows": [],
        }

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        # 0 means "not on the document" here (upstream steps store blanks as
        # 0), so it must not turn a real line amount into 0 x price = 0.
        qty = _nonzero(to_float(item.get("quantity")))
        unit_price = _nonzero(to_float(item.get("unit_price")))
        explicit_line_total = _nonzero(to_float(item.get("line_total")))

        chosen_line_total = None

        if qty is not None and unit_price is not None:
            chosen_line_total = qty * unit_price
        elif explicit_line_total is not None:
            chosen_line_total = explicit_line_total
        elif unit_price is not None:
            # Price with no quantity and no line total: one unit.
            chosen_line_total = unit_price

        if chosen_line_total is not None:
            chosen_line_total = round2(chosen_line_total)
            computed_total += chosen_line_total
            printed_line_total += explicit_line_total if explicit_line_total is not None else chosen_line_total
            valid_line_count += 1
            used_rows.append({
                "index": idx,
                "description": str(item.get("description", "")).strip(),
                "quantity": qty,
                "unit_price": unit_price,
                "line_total_used": chosen_line_total,
            })

    return {
        "computed_total": round2(computed_total) if valid_line_count > 0 else None,
        "printed_lines_total": round2(printed_line_total) if valid_line_count > 0 else None,
        "valid_line_count": valid_line_count,
        "used_rows": used_rows,
    }


def validate_arithmetic(fields: dict) -> dict:
    items = fields.get("items", []) if isinstance(fields, dict) else []
    items_result = compute_items_total(items)

    computed_items_total = items_result["computed_total"]
    raw_total = round2(to_float(fields.get("raw_total_amount")))
    final_total = round2(to_float(fields.get("final_total_amount")))
    payable_amount = round2(to_float(fields.get("payable_amount")))
    cash_return = round2(to_float(fields.get("cash_return")))
    tax_amount = _nonzero(round2(to_float(fields.get("tax_amount"))))
    discount_value = to_float(fields.get("discount_amount"))
    discount_amount = _nonzero(round2(abs(discount_value))) if discount_value is not None else None

    # A document total is consistent with its items if it equals the items sum
    # with or without the printed tax (exclusive vs. VAT-inclusive prices) and
    # discount. The printed line totals count too: a per-line discount makes
    # them lower than qty x unit price.
    expected_totals = []
    for base in (computed_items_total, items_result["printed_lines_total"]):
        if base is None:
            continue
        for adjustment in (0, tax_amount or 0, -(discount_amount or 0), (tax_amount or 0) - (discount_amount or 0)):
            expected_totals.append(round2(base + adjustment))

    items_net_total = None
    if computed_items_total is not None:
        items_net_total = round2(computed_items_total + (tax_amount or 0) - (discount_amount or 0))

    def _matches(value):
        return any(values_match(value, expected) for expected in expected_totals)

    checks = {
        "raw_total_matches_items": _matches(raw_total),
        "final_total_matches_items": _matches(final_total),
        "payable_matches_items": _matches(payable_amount),
    }

    # Totals the correction engine filled in FROM the items match them by
    # construction, so they prove nothing — only compare totals that were
    # actually read off the document.
    derived_totals = set(fields.get("derived_totals") or [])
    compared = [
        (field, value, checks[check])
        for field, value, check in (
            ("raw_total_amount", raw_total, "raw_total_matches_items"),
            ("final_total_amount", final_total, "final_total_matches_items"),
            ("payable_amount", payable_amount, "payable_matches_items"),
        )
        if value is not None and field not in derived_totals
    ]

    discrepancies = []
    if computed_items_total is not None:
        for field, value, matched in compared:
            if not matched:
                discrepancies.append({
                    "field": field,
                    "extracted": value,
                    "computed": computed_items_total,
                })

    recommended_raw_total = raw_total
    recommended_final_total = final_total
    recommended_payable_amount = payable_amount
    status = "not_checked"

    if computed_items_total is not None:
        # No document totals to compare → the totals were derived from the
        # items and are consistent with them.
        if not compared or any(matched for _, _, matched in compared):
            status = "matched"
        else:
            status = "mismatch"

            if items_result["valid_line_count"] > 0:
                # Items sum, less any printed discount, plus any printed tax.
                recommended_final_total = items_net_total
                recommended_payable_amount = items_net_total
    else:
        status = "low_confidence"

    # Delivery notes carry no amounts, so there is nothing to add up.
    if str(fields.get("document_type") or "").strip().lower() == "dn":
        status = "not_checked"
        discrepancies = []
        recommended_final_total = final_total
        recommended_payable_amount = payable_amount

    # Optional simple derived check for payable = final - cash_return
    payable_minus_cash = None
    if final_total is not None and cash_return is not None:
        payable_minus_cash = round2(final_total - cash_return)

        if payable_amount is None and payable_minus_cash is not None:
            payable_amount = payable_minus_cash

    return {
        "status": status,
        "computed_items_total": computed_items_total,
        "raw_total_amount": raw_total,
        "final_total_amount": final_total,
        "payable_amount": payable_amount,
        "cash_return": cash_return,
        "derived_payable_from_final_minus_cash_return": payable_minus_cash,
        "checks": checks,
        "valid_item_rows": items_result["valid_line_count"],
        "used_rows": items_result["used_rows"],
        "discrepancies": discrepancies,
        "recommended": {
            "raw_total_amount": recommended_raw_total,
            "final_total_amount": recommended_final_total,
            "payable_amount": recommended_payable_amount,
        }
    }


def _edit_figures(fields: dict) -> tuple:
    amounts = [
        compute_items_total(fields.get("items", []))["computed_total"],
        to_float(fields.get("final_total_amount")),
        to_float(fields.get("tax_amount")),
        to_float(fields.get("discount_amount")),
    ]
    return str(fields.get("document_type") or "").strip().lower(), [abs(a or 0.0) for a in amounts]


def revalidate_after_edit(before: dict, after: dict):
    """A fresh validation when a person changed the document's type, items or
    amounts, since their figures replace what was read off the paper. None when
    nothing changed, so saving as-is can't clear a real mismatch."""
    before_type, before_amounts = _edit_figures(before)
    after_type, after_amounts = _edit_figures(after)
    if before_type == after_type and all(values_match(a, b) for a, b in zip(before_amounts, after_amounts)):
        return None
    return validate_arithmetic(after)


def effective_arithmetic_status(record: dict) -> str:
    """A saved "mismatch" re-checked against the document's current items, tax and
    discount, for flags saved before those were kept or before the items were
    corrected. A real mismatch overwrites the final and payable totals with the
    items' total, so only the printed figures kept in the stored discrepancies are
    compared; the overwritten totals would always pass and hide the problem."""
    stored = str(record.get("arithmetic_status") or "")
    if stored.lower() != "mismatch":
        return stored
    validation = record.get("arithmetic_validation") or {}
    printed = {
        d["field"]: d["extracted"]
        for d in validation.get("discrepancies") or []
        if isinstance(d, dict) and d.get("field") and d.get("extracted") is not None
    }
    if not printed:
        return validate_arithmetic(record)["status"]
    no_totals = {"raw_total_amount": None, "final_total_amount": None, "payable_amount": None}
    return validate_arithmetic({**record, **no_totals, **printed})["status"]