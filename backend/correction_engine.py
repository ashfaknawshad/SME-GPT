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


def _amount_or_none(value):
    """Parse a money/quantity value; blank, unreadable or 0 means 'missing'."""
    number = safe_float(value, default=None)
    if number is None or number == 0:
        return None
    return number


def _complete_item_amounts(quantity, unit_price, line_total):
    """Fill in whichever of quantity / unit_price / line_total is missing from
    the others. Handwritten receipts often show one amount per line
    ("Glue ... 110") with no qty or unit price: that is 1 x 110 = 110."""
    if quantity is None:
        if unit_price is not None and line_total is not None:
            quantity = round(line_total / unit_price, 2)
        elif unit_price is not None or line_total is not None:
            quantity = 1.0

    if unit_price is None and line_total is not None and quantity:
        unit_price = round(line_total / quantity, 2)

    if line_total is None and unit_price is not None and quantity:
        line_total = round(quantity * unit_price, 2)

    return quantity, unit_price, line_total


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
        # Blank / unreadable values stay None ("missing"), never 0 — a 0 here used
        # to make the validator compute qty*price = 0 and wipe the real total.
        quantity = _amount_or_none(item.get("quantity"))
        unit_price = _amount_or_none(item.get("unit_price"))
        line_total = _amount_or_none(item.get("line_total"))
        quantity, unit_price, line_total = _complete_item_amounts(quantity, unit_price, line_total)

        # The line-total column was copied into unit_price (e.g. qty 2, unit 440,
        # total 440 on a receipt that printed 220 / 440): derive the real unit price.
        if (quantity and line_total and unit_price and abs(quantity - 1) > 1e-9
                and abs(unit_price - line_total) <= 0.01):
            unit_price = round(line_total / quantity, 2)

        corrected_desc_result = hybrid_correct_text(description)
        corrected_description = corrected_desc_result["final_text"]

        corrected_description = re.sub(r"^\s*Corrected OCR text:\s*", "", corrected_description, flags=re.IGNORECASE)
        corrected_description = re.sub(r"^\s*Corrected text:\s*", "", corrected_description, flags=re.IGNORECASE)
        corrected_description = re.sub(r"^\s*OCR text:\s*", "", corrected_description, flags=re.IGNORECASE)
        corrected_description = corrected_description.strip()

        normalized.append({
            "description": corrected_description,
            "quantity": quantity if quantity is not None else 0.0,
            "unit_price": unit_price if unit_price is not None else 0.0,
            "line_total": round(line_total, 2) if line_total is not None else 0.0,
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


_PERCENT_SUFFIX = r"\s*(?:\(?\s*\d+(?:\.\d+)?\s*%?\s*\)?)?\s*:?\s*$"
_TAX_ROW_RE = re.compile(r"^\s*(?:vat|nbt|sscl|gst|tax|බදු|වැට්)" + _PERCENT_SUFFIX, re.IGNORECASE)
_DISCOUNT_ROW_RE = re.compile(
    r"^\s*(?:less\s*:?\s*)?(?:trade\s+|cash\s+)?(?:discount|disc\.?|වට්ටම්)" + _PERCENT_SUFFIX, re.IGNORECASE
)
_TAX_LABEL_RE = re.compile(r"\b(?:vat|nbt|sscl|gst|tax)\b|බදු|වැට්", re.IGNORECASE)
_DISCOUNT_LABEL_RE = re.compile(r"\b(?:discount|disc\.?)(?!\w)|වට්ටම්", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_TOTALS_TOLERANCE = 0.05


def _split_adjustment_rows(items, data: dict):
    """Rows that are really the document's VAT or discount ("VAT 18% ... 1,062")
    aren't items: move them to their own fields so they don't inflate the subtotal."""
    if not isinstance(items, list):
        return items
    kept = []
    for item in items:
        description = str(item.get("description", "")) if isinstance(item, dict) else ""
        field = "tax_amount" if _TAX_ROW_RE.match(description) else (
            "discount_amount" if _DISCOUNT_ROW_RE.match(description) else None
        )
        amount = None
        if field:
            amount = _amount_or_none(item.get("line_total")) or _amount_or_none(item.get("unit_price"))
        if amount is None:
            kept.append(item)
        elif _amount_or_none(data.get(field)) is None:
            data[field] = abs(amount)
    return kept


def _labelled_amounts(text: str, label_re) -> set[float]:
    """Amounts on, or on the line after, a line carrying the label. OCR often
    puts a right-aligned amount on the line below its label."""
    lines = str(text or "").splitlines()
    found: set[float] = set()
    for i, line in enumerate(lines):
        if label_re.search(line):
            for candidate in [line] + lines[i + 1:i + 2]:
                for token in _AMOUNT_RE.findall(candidate):
                    found.add(float(token.replace(",", "")))
    return found


def _resolve_tax_and_discount(data: dict, items_total: float) -> tuple[float, float]:
    """The document's tax and discount, recovering what extraction missed. A
    figure read back from the OCR text or a printed rate is only accepted when it
    makes the items reconcile with the printed total, so a stray number can't
    change a document's amounts."""
    tax = abs(safe_float(data.get("tax_amount")))
    discount = abs(safe_float(data.get("discount_amount")))
    if items_total <= 0:
        return tax, discount

    tax_rate = safe_float(data.get("tax_rate"))
    discount_rate = safe_float(data.get("discount_rate"))
    rate_discount = round(items_total * discount_rate / 100, 2) if discount_rate > 0 else 0.0

    def rate_tax(applied_discount: float) -> float:
        return round((items_total - applied_discount) * tax_rate / 100, 2) if tax_rate > 0 else 0.0

    final_total = safe_float(data.get("final_total_amount"))
    if final_total <= 0:
        # Nothing printed to reconcile against: apply the printed rates.
        discount = discount or rate_discount
        return tax or rate_tax(discount), discount

    def reconciles(candidate_tax: float, candidate_discount: float) -> bool:
        return abs(items_total - candidate_discount + candidate_tax - final_total) <= _TOTALS_TOLERANCE

    if reconciles(tax, discount) or abs(items_total - final_total) <= _TOTALS_TOLERANCE:
        return tax, discount

    text = "\n".join(str(data.get(k) or "") for k in ("corrected_text", "raw_text"))
    discounts = {discount} if discount else {0.0, rate_discount} | _labelled_amounts(text, _DISCOUNT_LABEL_RE)
    for candidate_discount in sorted(discounts):
        taxes = {tax} if tax else {0.0, rate_tax(candidate_discount)} | _labelled_amounts(text, _TAX_LABEL_RE)
        for candidate_tax in sorted(taxes):
            if reconciles(candidate_tax, candidate_discount):
                return candidate_tax, candidate_discount

    # A printed subtotal equal to the items, above the final total: the
    # difference is a discount the OCR text didn't label clearly.
    raw_total = safe_float(data.get("raw_total_amount"))
    if not discount and abs(raw_total - items_total) <= _TOTALS_TOLERANCE and final_total < raw_total + tax:
        discount = round(raw_total + tax - final_total, 2)
    return tax, discount


def correct_extracted_fields(extracted_json: dict):
    data = deepcopy(extracted_json)

    items = _split_adjustment_rows(data.get("items", []), data)
    normalized_items = normalize_items(items)

    data["items"] = normalized_items
    recalculated_total = recalculate_totals(normalized_items)

    tax_amount, discount_amount = _resolve_tax_and_discount(data, recalculated_total)
    if tax_amount:
        data["tax_amount"] = tax_amount
    if discount_amount:
        data["discount_amount"] = discount_amount
    net_total = round(recalculated_total - discount_amount + tax_amount, 2)

    existing_final_total = safe_float(data.get("final_total_amount", 0))
    # Items already include the VAT (a printed total equal to the items net of
    # discount): the UI must not add it a second time.
    data["tax_inclusive"] = bool(
        tax_amount and existing_final_total
        and abs(recalculated_total - discount_amount - existing_final_total) <= _TOTALS_TOLERANCE
    )
    existing_payable_amount = safe_float(data.get("payable_amount", 0))

    # Record which totals were filled in here rather than read off the
    # document, so the arithmetic validator only checks the items against
    # totals actually printed (comparing the items with a number built from
    # those same items always "matched" and hid real mismatches).
    derived_totals = []
    if recalculated_total > 0:
        if existing_final_total == 0:
            data["final_total_amount"] = net_total
            derived_totals.append("final_total_amount")
        if existing_payable_amount == 0:
            # Payable is the document total (same rule dataset_manager applies
            # on save), falling back to the items' net total when none is printed.
            data["payable_amount"] = existing_final_total or net_total
            derived_totals.append("payable_amount")

        data["recommended_total_from_items"] = recalculated_total
    data["derived_totals"] = derived_totals
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