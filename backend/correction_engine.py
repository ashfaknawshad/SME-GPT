import re
from copy import deepcopy
from datetime import date as _date_today
from llm_correction import llm_refine_text, dictionary_correct_text

def safe_float(value, default=0.0):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return default

    text = text.replace(",", "").replace("Rs.", "").replace("Rs", "").replace("LKR", "").replace("$", "").strip()
    try:
        return float(text)
    except Exception:
        return default


def hybrid_correct_text(text: str):
    dictionary_text = dictionary_correct_text(text)

    try:
        llm_text = llm_refine_text(dictionary_text)
    except Exception:
        llm_text = dictionary_text

    return {
        "original_text": text,
        "dictionary_text": dictionary_text,
        "llm_text": llm_text,
        "final_text": llm_text,
        "confidence_score": 0.92 if llm_text != text else 0.85,
    }


def normalize_items(items):
    if not isinstance(items, list):
        return []

    normalized = []

    for item in items:
        if not isinstance(item, dict):
            continue

        description = str(item.get("description", "")).strip()
        description = re.sub(r"^\s*Corrected OCR text:\s*", "", description, flags=re.IGNORECASE)
        description = re.sub(r"^\s*Corrected text:\s*", "", description, flags=re.IGNORECASE)
        description = re.sub(r"^\s*OCR text:\s*", "", description, flags=re.IGNORECASE)
        quantity = safe_float(item.get("quantity", 0))
        unit_price = safe_float(item.get("unit_price", 0))
        line_total = safe_float(item.get("line_total", 0))

        corrected_desc_result = hybrid_correct_text(description)
        corrected_description = corrected_desc_result["final_text"]

        corrected_description = re.sub(r"^\s*Corrected OCR text:\s*", "", corrected_description, flags=re.IGNORECASE)
        corrected_description = re.sub(r"^\s*Corrected text:\s*", "", corrected_description, flags=re.IGNORECASE)
        corrected_description = re.sub(r"^\s*OCR text:\s*", "", corrected_description, flags=re.IGNORECASE)
        corrected_description = corrected_description.strip()

        # The line-total column was copied into unit_price (e.g. qty 2, unit 440,
        # total 440 on a receipt that printed 220 / 440): derive the real unit price.
        if (quantity > 0 and abs(quantity - 1) > 1e-9 and line_total > 0
                and abs(unit_price - line_total) <= 0.01):
            unit_price = round(line_total / quantity, 2)

        if quantity > 0 and unit_price > 0 and line_total == 0:
            computed_line_total = round(quantity * unit_price, 2)
        else:
            computed_line_total = round(line_total, 2)

        normalized.append({
            "description": corrected_description,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": computed_line_total,
            "correction_confidence": corrected_desc_result["confidence_score"]
        })

    return normalized


def recalculate_totals(items):
    if not isinstance(items, list):
        return 0.0

    total = 0.0
    for item in items:
        total += safe_float(item.get("line_total", 0))
    return round(total, 2)


def correct_extracted_fields(extracted_json: dict):
    data = deepcopy(extracted_json)

    items = data.get("items", [])
    normalized_items = normalize_items(items)

    data["items"] = normalized_items
    recalculated_total = recalculate_totals(normalized_items)

    existing_final_total = safe_float(data.get("final_total_amount", 0))
    existing_payable_amount = safe_float(data.get("payable_amount", 0))

    if recalculated_total > 0:
        if existing_final_total == 0:
            data["final_total_amount"] = recalculated_total
        if existing_payable_amount == 0:
            data["payable_amount"] = recalculated_total

        data["recommended_total_from_items"] = recalculated_total
    data["correction_status"] = "hybrid_text_and_item_normalization"
    data["correction_confidence"] = 0.92

    _derive_workflow_status(data)

    return data


def _parse_date_flexible(date_str: str):
    """Try common date formats, return date object or None."""
    if not date_str or str(date_str).strip().upper() in ("", "NULL", "NONE"):
        return None
    s = str(date_str).strip()
    try:
        # ISO YYYY-MM-DD must be parsed first — dayfirst=True mangles it to YYYY-DD-MM
        return _date_today.fromisoformat(s)
    except ValueError:
        pass
    from dateutil import parser as _dparser
    try:
        return _dparser.parse(s, dayfirst=True).date()
    except Exception:
        return None


def _derive_workflow_status(data: dict):
    """
    Iteration 10: derive po_status, dn_status, invoice_status from existing fields.
    Called after item/total corrections so paid_status/received_status are final.
    """
    doc_type  = str(data.get("document_type", "")).strip().lower()
    paid      = str(data.get("paid_status",     "")).strip().lower()
    received  = str(data.get("received_status", "")).strip().lower()
    today     = _date_today.today()

    if doc_type == "po":
        if not data.get("po_status") or str(data.get("po_status", "")).strip().upper() in ("", "NULL"):
            if paid == "paid":
                data["po_status"] = "fulfilled"
            elif paid == "partial":
                data["po_status"] = "partially_delivered"
            else:
                # Check if overdue by delivery_date
                delivery_dt = _parse_date_flexible(data.get("delivery_date", ""))
                if delivery_dt and delivery_dt < today:
                    data["po_status"] = "pending"  # overdue but still pending
                else:
                    data["po_status"] = "pending"

    elif doc_type == "dn":
        if not data.get("dn_status") or str(data.get("dn_status", "")).strip().upper() in ("", "NULL"):
            if received == "received":
                data["dn_status"] = "delivered"
            elif received in ("partial", "partially_received"):
                data["dn_status"] = "partially_delivered"
            else:
                delivery_dt = _parse_date_flexible(data.get("delivery_date", ""))
                if delivery_dt and delivery_dt < today:
                    data["dn_status"] = "delayed"
                else:
                    data["dn_status"] = "pending"

    elif doc_type == "invoice":
        if not data.get("invoice_status") or str(data.get("invoice_status", "")).strip().upper() in ("", "NULL"):
            due_dt = _parse_date_flexible(data.get("due_date", ""))
            if paid == "paid":
                data["invoice_status"] = "paid"
            elif paid == "partial":
                data["invoice_status"] = "partially_paid"
            elif due_dt and due_dt < today:
                data["invoice_status"] = "overdue"
            else:
                data["invoice_status"] = "pending"