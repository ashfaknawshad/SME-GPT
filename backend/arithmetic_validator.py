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


def compute_items_total(items: List[dict]) -> Dict[str, Any]:
    computed_total = 0.0
    valid_line_count = 0
    used_rows = []

    if not isinstance(items, list):
        return {
            "computed_total": None,
            "valid_line_count": 0,
            "used_rows": [],
        }

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        qty = to_float(item.get("quantity"))
        unit_price = to_float(item.get("unit_price"))
        explicit_line_total = to_float(item.get("line_total"))

        chosen_line_total = None

        # The printed line total is what the document actually charged; qty × unit
        # price is only a fallback (a misread unit-price column would otherwise
        # multiply every multi-quantity row a second time).
        if explicit_line_total:
            chosen_line_total = explicit_line_total
        elif qty is not None and unit_price is not None:
            chosen_line_total = qty * unit_price

        if chosen_line_total is not None:
            chosen_line_total = round2(chosen_line_total)
            computed_total += chosen_line_total
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

    checks = {
        "raw_total_matches_items": values_match(raw_total, computed_items_total),
        "final_total_matches_items": values_match(final_total, computed_items_total),
        "payable_matches_items": values_match(payable_amount, computed_items_total),
    }

    discrepancies = []
    if computed_items_total is not None:
        if raw_total is not None and not checks["raw_total_matches_items"]:
            discrepancies.append({
                "field": "raw_total_amount",
                "extracted": raw_total,
                "computed": computed_items_total,
            })

        if final_total is not None and not checks["final_total_matches_items"]:
            discrepancies.append({
                "field": "final_total_amount",
                "extracted": final_total,
                "computed": computed_items_total,
            })

        if payable_amount is not None and not checks["payable_matches_items"]:
            discrepancies.append({
                "field": "payable_amount",
                "extracted": payable_amount,
                "computed": computed_items_total,
            })

    recommended_raw_total = raw_total
    recommended_final_total = final_total
    recommended_payable_amount = payable_amount
    status = "not_checked"

    if computed_items_total is not None:
        if checks["raw_total_matches_items"] or checks["final_total_matches_items"] or checks["payable_matches_items"]:
            status = "matched"
        else:
            status = "mismatch"

            if items_result["valid_line_count"] > 0:
                recommended_final_total = computed_items_total
                recommended_payable_amount = computed_items_total
    else:
        status = "low_confidence"

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