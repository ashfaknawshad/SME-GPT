"""Financial document data-access layer (Postgres / Supabase).

Iteration 1: replaces the previous CSV store with the Prisma-managed Postgres
schema (FinancialDocument + LineItem). The public API and the record shape are
preserved so app.py and data_tools.py keep working unchanged:

- records are dicts keyed by DATASET_COLUMNS (snake_case)
- missing values are the string "NULL"
- line items live in `items_json` (a JSON string)
- amounts are floats or "NULL"

All reads/writes are tenant-scoped (tenant_id == user_id). Deletes are soft
(`deletedAt`); soft-deleted rows are excluded everywhere.
"""

import os
import json
from datetime import datetime, timedelta

import pandas as pd
from psycopg.types.json import Jsonb

from arithmetic_validator import effective_arithmetic_status
from db import get_conn, new_id, run_with_retry

INCOMING_JSON_DIR = "incoming_json"

DATASET_COLUMNS = [
    "user_id",
    "document_id",
    "document_type",
    "order_id",
    "po_reference",
    "flow_type",
    "effective_flow_type",
    "company_name",
    "supplier_name",
    "date",
    "raw_total_amount",
    "final_total_amount",
    "total_status",
    "payable_amount",
    "cash_return",
    "tax_amount",
    "tax_rate",
    "cash_inflowed",
    "cash_outflowed",
    "currency",
    "received_status",
    "paid_status",
    "status",
    "language",
    "raw_text",
    "corrected_text",
    "items_json",
    "structured_json",
    "correction_json",
    "arithmetic_status",
    "arithmetic_json",
    "ocr_selected_version",
    "safe_boxes_json",
    "spatial_chunks_json",
    "file_size_kb",
    "field_chunk_map_json",
    # Iteration 10 — PO/DN workflow + invoice status
    "po_status",
    "dn_status",
    "invoice_status",
    "due_date",
    "delivery_date",
    "approved_by",
    "proof_of_delivery",
    "signed",
    # Iteration 11 — supplier/customer contact fields
    "supplier_city",
    "supplier_phone",
    "supplier_email",
    "company_city",
    "company_phone",
    "company_email",
    # Migration 012 — own number vs. references (document linking)
    "doc_number",
    "po_ref",
    "dn_ref",
    "invoice_ref",
]

# record key -> DB column (Prisma camelCase). items_json is handled separately.
RECORD_TO_DB = {
    "user_id": "tenantId",
    "document_id": "documentId",
    "document_type": "documentType",
    "order_id": "orderId",
    # PO number a DN / invoice belongs to (links the order; see order_reconciliation)
    "po_reference": "poReference",
    "flow_type": "flowType",
    "effective_flow_type": "effectiveFlowType",
    "company_name": "companyName",
    "supplier_name": "supplierName",
    "date": "docDate",
    "raw_total_amount": "rawTotalAmount",
    "final_total_amount": "finalTotalAmount",
    "total_status": "totalStatus",
    "payable_amount": "payableAmount",
    "cash_return": "cashReturn",
    "tax_amount": "taxAmount",
    "tax_rate": "taxRate",
    "cash_inflowed": "cashInflowed",
    "cash_outflowed": "cashOutflowed",
    "category": "category",
    "currency": "currency",
    "received_status": "receivedStatus",
    "paid_status": "paidStatus",
    "status": "status",
    "language": "language",
    "raw_text": "rawText",
    "corrected_text": "correctedText",
    "structured_json": "structuredJson",
    "correction_json": "correctionJson",
    "arithmetic_status": "arithmeticStatus",
    "arithmetic_json": "arithmeticJson",
    "ocr_selected_version": "ocrSelectedVersion",
    # Iteration 9 — spatial blobs (stored as TEXT, not JSONB)
    "safe_boxes_json": "safeboxJson",
    "spatial_chunks_json": "spatialChunksJson",
    "file_size_kb": "fileSizeKb",
    "field_chunk_map_json": "fieldChunkMapJson",
    # Iteration 10 — PO/DN workflow + invoice status
    "po_status": "poStatus",
    "dn_status": "dnStatus",
    "invoice_status": "invoiceStatus",
    "due_date": "dueDate",
    "delivery_date": "deliveryDate",
    "approved_by": "approvedBy",
    "proof_of_delivery": "proofOfDelivery",
    "signed": "signed",
    # Iteration 11 — supplier/customer contact fields
    "supplier_city": "supplierCity",
    "supplier_phone": "supplierPhone",
    "supplier_email": "supplierEmail",
    "company_city": "companyCity",
    "company_phone": "companyPhone",
    "company_email": "companyEmail",
    # Migration 012 — own number vs. references (document linking)
    "doc_number": "docNumber",
    "po_ref": "poRef",
    "dn_ref": "dnRef",
    "invoice_ref": "invoiceRef",
}

MONEY_FIELDS = {"raw_total_amount", "final_total_amount", "payable_amount", "cash_return", "tax_amount", "tax_rate", "cash_inflowed", "cash_outflowed"}
JSON_FIELDS = {"structured_json", "correction_json", "arithmetic_json"}
BOOL_FIELDS = {"proof_of_delivery", "signed"}


# ──────────────────────────── pure helpers (unchanged) ────────────────────────────

def save_input_json(json_data: dict, filename: str = "last_confirmed.json"):
    os.makedirs(INCOMING_JSON_DIR, exist_ok=True)
    path = os.path.join(INCOMING_JSON_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    return path


def flatten_text(text):
    if text is None:
        return "NULL"
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\n", " ")
    text = " ".join(text.split())
    text = text.strip()
    return text if text else "NULL"


def nullify_text(value):
    if value is None:
        return "NULL"
    text = str(value).strip()
    return text if text else "NULL"


def safe_to_float_or_null(value):
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return "NULL"
    text = text.replace(",", "").replace("Rs.", "").replace("Rs", "")
    text = text.replace("LKR", "").replace("$", "").strip()
    if not text:
        return "NULL"
    try:
        return float(text)
    except Exception:
        return "NULL"


def normalize_compare_text(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    return " ".join(text.split())


def normalize_compare_number(value):
    parsed = safe_to_float_or_null(value)
    if parsed == "NULL":
        return "NULL"
    return float(parsed)


def normalize_items(items):
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        quantity = safe_to_float_or_null(item.get("quantity"))
        unit_price = safe_to_float_or_null(item.get("unit_price"))
        line_total = safe_to_float_or_null(item.get("line_total"))
        # Guard: a stored line total that isn't quantity * unit_price is an
        # extraction or edit error — the two silently disagree (e.g. qty 25,
        # unit 89, but line_total 800). When both operands are real positive
        # numbers, recompute the line total so a row can never persist a
        # contradictory value. Rows missing a qty or unit keep the extracted
        # line total (e.g. a lump-sum charge with no unit price).
        if isinstance(quantity, float) and isinstance(unit_price, float) and quantity > 0 and unit_price > 0:
            line_total = round(quantity * unit_price, 2)
        result.append({
            "description": str(item.get("description", "")).strip(),
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": line_total,
        })
    return result


def get_prefix_for_document_type(document_type: str) -> str:
    if not isinstance(document_type, str):
        return "DOC"
    doc_type = document_type.lower().strip()
    prefix_map = {"receipt": "R", "invoice": "IN", "po": "PO", "dn": "DN"}
    return prefix_map.get(doc_type, "DOC")


def is_exact_duplicate(existing_row: dict, new_record: dict) -> bool:
    compare_text_fields = [
        "user_id", "document_type", "order_id", "flow_type", "company_name",
        "supplier_name", "date", "currency", "received_status", "paid_status",
        "status", "language", "raw_text", "corrected_text", "items_json",
    ]
    compare_number_fields = [
        "raw_total_amount", "final_total_amount", "payable_amount", "cash_return",
    ]
    for field in compare_text_fields:
        if normalize_compare_text(existing_row.get(field)) != normalize_compare_text(new_record.get(field)):
            return False
    for field in compare_number_fields:
        if normalize_compare_number(existing_row.get(field)) != normalize_compare_number(new_record.get(field)):
            return False
    return True


def normalize_record(data: dict, user_id: str, force_generate_document_id: bool = True) -> dict:
    document_type = nullify_text(data.get("document_type", None))
    if document_type == "NULL":
        document_type = "unknown"

    if force_generate_document_id:
        document_id = generate_document_id(document_type)
    else:
        document_id = nullify_text(data.get("document_id", None))

    raw_text = flatten_text(data.get("raw_text", None))
    corrected_text = flatten_text(data.get("corrected_text", None))

    raw_total = safe_to_float_or_null(data.get("raw_total_amount", None))
    final_total = safe_to_float_or_null(data.get("final_total_amount", None))
    payable_amount = safe_to_float_or_null(data.get("payable_amount", None))
    cash_return = safe_to_float_or_null(data.get("cash_return", None))

    # Guard: for documents whose payable/receivable IS the document total
    # (invoice, receipt, po), keep payable_amount equal to the final total.
    # Without this the arithmetic validator's item-sum recommendation could
    # leave payable_amount disagreeing with final_total_amount — e.g. payable
    # 9307 (sum of qty*unit) while final total says 7641 (the printed total).
    if document_type in ("invoice", "receipt", "po") and isinstance(final_total, float):
        payable_amount = final_total

    if raw_total == "NULL" or final_total == "NULL":
        total_status = "NULL"
    else:
        total_status = "corrected" if final_total != raw_total else "original"

    items = normalize_items(data.get("items", []))
    arithmetic_validation = data.get("arithmetic_validation", {}) or {}

    structured_json = json.dumps(data, ensure_ascii=False).replace("\n", " ")
    correction_json = json.dumps({
        "total_check": {
            "raw_total_amount": raw_total,
            "final_total_amount": final_total,
            "status": total_status,
            "payable_amount": payable_amount,
            "cash_return": cash_return,
        }
    }, ensure_ascii=False).replace("\n", " ")
    arithmetic_json = json.dumps(arithmetic_validation, ensure_ascii=False).replace("\n", " ")

    return {
        "user_id": nullify_text(user_id),
        "document_id": document_id,
        "document_type": document_type,
        "order_id": nullify_text(data.get("order_id", None)),
        "po_reference": nullify_text(data.get("po_reference", None)),
        "flow_type": nullify_text(data.get("flow_type", None)),
        "effective_flow_type": nullify_text(
            data.get("effective_flow_type", data.get("flow_type", None))
        ),
        "company_name": nullify_text(data.get("company_name", None)),
        "supplier_name": nullify_text(data.get("supplier_name", None)),
        "date": nullify_text(data.get("date", None)),
        "raw_total_amount": raw_total,
        "final_total_amount": final_total,
        "total_status": total_status,
        "payable_amount": payable_amount,
        "cash_return": cash_return,
        "tax_amount": safe_to_float_or_null(data.get("tax_amount", None)),
        "tax_rate": safe_to_float_or_null(data.get("tax_rate", None)),
        "cash_inflowed": safe_to_float_or_null(data.get("cash_inflowed", None)),
        "cash_outflowed": safe_to_float_or_null(data.get("cash_outflowed", None)),
        "currency": nullify_text(data.get("currency", None)),
        "received_status": nullify_text(data.get("received_status", None)),
        "paid_status": nullify_text(data.get("paid_status", None)),
        "status": nullify_text(data.get("status", None)),
        "language": nullify_text(data.get("language", None)),
        "raw_text": raw_text,
        "corrected_text": corrected_text,
        "items_json": json.dumps(items, ensure_ascii=False).replace("\n", " "),
        "structured_json": structured_json if structured_json.strip() else "NULL",
        "correction_json": correction_json if correction_json.strip() else "NULL",
        "arithmetic_status": nullify_text(data.get("arithmetic_status", "not_checked")),
        "arithmetic_json": arithmetic_json if arithmetic_json.strip() else "NULL",
        "ocr_selected_version": nullify_text(data.get("ocr_selected_version", None)),
        # Iteration 9 — spatial blobs (large TEXT strings; NULL until doc is confirmed)
        "safe_boxes_json": nullify_text(data.get("safe_boxes_json", None)),
        "spatial_chunks_json": nullify_text(data.get("spatial_chunks_json", None)),
        # Iteration 10 — PO/DN workflow + invoice status
        "po_status": nullify_text(data.get("po_status", None)),
        "dn_status": nullify_text(data.get("dn_status", None)),
        "invoice_status": nullify_text(data.get("invoice_status", None)),
        "due_date": nullify_text(data.get("due_date", None)),
        "delivery_date": nullify_text(data.get("delivery_date", None)),
        "approved_by": nullify_text(data.get("approved_by", None)),
        "proof_of_delivery": data.get("proof_of_delivery", None),
        "signed": data.get("signed", None),
        # Iteration 11 — supplier/customer contact fields
        "supplier_city": nullify_text(data.get("supplier_city", None)),
        "supplier_phone": nullify_text(data.get("supplier_phone", None)),
        "supplier_email": nullify_text(data.get("supplier_email", None)),
        "company_city": nullify_text(data.get("company_city", None)),
        "company_phone": nullify_text(data.get("company_phone", None)),
        "company_email": nullify_text(data.get("company_email", None)),
        # Migration 012 — own number vs. references (document linking)
        "doc_number": nullify_text(data.get("doc_number", None)),
        "po_ref": nullify_text(data.get("po_ref", None)),
        "dn_ref": nullify_text(data.get("dn_ref", None)),
        "invoice_ref": nullify_text(data.get("invoice_ref", None)),
    }


def _derive_category_from_flow(flow_type: str) -> str:
    ft = str(flow_type or "").strip().lower().replace(" ", "_")
    if ft in ("receivable", "cash_inflow"):
        return "Revenue"
    if ft in ("payable", "cash_outflow"):
        return "Expenses"
    return "Unknown"


def parse_record_for_output(record: dict):
    parsed = dict(record)
    try:
        items = json.loads(parsed.get("items_json", "[]"))
        parsed["items"] = items if isinstance(items, list) else []
    except Exception:
        parsed["items"] = []
    try:
        arithmetic = json.loads(parsed.get("arithmetic_json", "{}"))
        parsed["arithmetic_validation"] = arithmetic if isinstance(arithmetic, dict) else {}
    except Exception:
        parsed["arithmetic_validation"] = {}

    # Discount has no DB column, and documents saved before tax was persisted
    # have it only in structured_json: surface both from there.
    try:
        structured = json.loads(parsed.get("structured_json") or "{}")
    except Exception:
        structured = {}
    if isinstance(structured, dict):
        for field in ("discount_amount", "tax_amount", "tax_rate"):
            if str(parsed.get(field) or "").strip().upper() in ("", "NULL", "NONE", "0", "0.0"):
                if structured.get(field) not in (None, "", "NULL"):
                    parsed[field] = structured[field]

    parsed["arithmetic_status"] = effective_arithmetic_status(parsed)

    # Always compute category live — DB may be NULL for older records
    if not parsed.get("category") or parsed["category"] in ("NULL", "Unknown"):
        eft = parsed.get("effective_flow_type") or parsed.get("flow_type") or ""
        parsed["category"] = _derive_category_from_flow(eft)

    return parsed


# ──────────────────────────── DB <-> record conversion ────────────────────────────

def _to_db_text(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.upper() == "NULL":
        return None
    return text


def _to_db_money(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if text == "" or text.upper() == "NULL":
        return None
    try:
        return float(text.replace(",", ""))
    except Exception:
        return None


def _to_db_json(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return Jsonb(value)
    text = str(value).strip()
    if text == "" or text.upper() == "NULL":
        return None
    try:
        return Jsonb(json.loads(text))
    except Exception:
        return None


def _from_db_text(value):
    if value is None:
        return "NULL"
    text = str(value)
    return text if text.strip() else "NULL"


def _from_db_money(value):
    if value is None:
        return "NULL"
    return float(value)


def _from_db_json(value):
    if value is None:
        return "NULL"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _to_db_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no", "null", ""):
        return None
    return None


def _from_db_bool(value):
    if value is None:
        return None
    return bool(value)


def _record_to_db_value(record_key, value):
    if record_key in MONEY_FIELDS:
        return _to_db_money(value)
    if record_key in JSON_FIELDS:
        return _to_db_json(value)
    if record_key in BOOL_FIELDS:
        return _to_db_bool(value)
    return _to_db_text(value)


def _li_to_item(li: dict) -> dict:
    return {
        "description": (li.get("description") or "").strip() if li.get("description") else "",
        "quantity": _from_db_money(li.get("qty")),
        "unit_price": _from_db_money(li.get("unitPrice")),
        "line_total": _from_db_money(li.get("total")),
    }


def _row_to_record(row: dict, items: list) -> dict:
    record = {}
    for rec_key, db_col in RECORD_TO_DB.items():
        raw = row.get(db_col)
        if rec_key in MONEY_FIELDS:
            record[rec_key] = _from_db_money(raw)
        elif rec_key in JSON_FIELDS:
            record[rec_key] = _from_db_json(raw)
        elif rec_key in BOOL_FIELDS:
            record[rec_key] = _from_db_bool(raw)
        else:
            record[rec_key] = _from_db_text(raw)
    record["items_json"] = json.dumps(items, ensure_ascii=False)
    return record


# ──────────────────────────── reads ────────────────────────────

def parse_date_bounds(date_from: str | None, date_to: str | None):
    """Parse ISO ``YYYY-MM-DD`` strings into inclusive createdAt timestamp bounds.

    Returns ``(lower, upper)`` where ``lower`` is the start of ``date_from`` and
    ``upper`` is the start of the day *after* ``date_to`` (so the range is
    inclusive of the whole ``date_to`` day). Either bound is ``None`` when its
    input is missing or unparseable — callers simply skip that side of the range.
    Pure function (no DB) so it is unit-testable without a database.
    """
    def _start_of_day(value):
        if not value:
            return None
        try:
            return datetime.strptime(str(value).strip(), "%Y-%m-%d")
        except (ValueError, TypeError):
            return None

    lower = _start_of_day(date_from)
    upper = _start_of_day(date_to)
    if upper is not None:
        upper = upper + timedelta(days=1)  # make date_to inclusive
    return lower, upper


def _created_at_clause(date_from, date_to, where: list, params: list) -> None:
    """No-op placeholder kept for API compatibility.

    Date filtering by document date is done in Python after DB fetch via
    ``filter_records_by_doc_date()`` because OCR dates arrive in many formats
    (DD/MM/YYYY, MM/DD/YYYY, DD MMM YYYY, YYYY-MM-DD, etc.) and cannot be
    reliably compared with a SQL text predicate.
    """
    # Intentionally empty — see filter_records_by_doc_date()


# Multi-format date parsers tried in order (most common first)
_DATE_FORMATS = [
    "%Y-%m-%d",   # 2026-04-15  (ISO — from date picker)
    "%d/%m/%Y",   # 15/04/2026  (Sri Lankan / UK)
    "%m/%d/%Y",   # 04/15/2026  (US)
    "%d-%m-%Y",   # 15-04-2026
    "%Y/%m/%d",   # 2026/04/15
]

# Locale-independent English month name lookup (strptime %b/%B is locale-sensitive)
_EN_MONTHS = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
    "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
    "january":1,"february":2,"march":3,"april":4,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
}


def _parse_named_month(val: str) -> "date | None":
    """Parse dates with English written month names, locale-independently.

    Handles: '15 Apr 2026', '15 April 2026', 'Apr 15, 2026', 'April 15, 2026'
    """
    from datetime import date as _date
    import re
    val = val.replace(",", "").strip()
    parts = re.split(r"[\s]+", val)
    if len(parts) != 3:
        return None
    a, b, c = parts[0].lower(), parts[1].lower(), parts[2].lower()
    try:
        # "15 Apr 2026" → a=day(digit), b=month(word), c=year
        if a.isdigit() and (a not in _EN_MONTHS) and b in _EN_MONTHS and c.isdigit():
            return _date(int(c), _EN_MONTHS[b], int(a))
        # "Apr 15 2026" → a=month(word), b=day(digit), c=year
        if a in _EN_MONTHS and b.isdigit() and c.isdigit():
            return _date(int(c), _EN_MONTHS[a], int(b))
    except (ValueError, KeyError):
        pass
    return None


def _parse_doc_date(raw: str) -> "date | None":
    """Try every known date format; return a date object or None."""
    from datetime import date as _date
    val = str(raw or "").strip()
    if not val or val.upper() in ("NULL", "NONE", ""):
        return None
    # Strip trailing time component (e.g. "2026-04-15 00:00:00" or "2026-04-15T00:00:00")
    val = val.split("T")[0].strip()

    # Try named-month parser first (locale-independent)
    named = _parse_named_month(val)
    if named:
        return named

    # Strip time portion after a space (for "15/04/2026 14:30:00" style)
    val_date_only = val.split(" ")[0].strip()

    for fmt in _DATE_FORMATS:
        for candidate in (val_date_only, val):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def filter_records_by_doc_date(
    records: list,
    date_from: "str | None" = None,
    date_to: "str | None" = None,
) -> list:
    """Filter an in-memory record list by docDate using multi-format parsing.

    Records whose date cannot be parsed are included (better to over-include
    than silently exclude legitimate documents).
    """
    if not date_from and not date_to:
        return records

    lower, upper = parse_date_bounds(date_from, date_to)
    lo = lower.date() if lower is not None else None
    hi = upper.date() if upper is not None else None   # exclusive upper (day after date_to)

    result = []
    for r in records:
        raw = str(r.get("docDate") or r.get("date") or "").strip()
        doc_date = _parse_doc_date(raw)
        if doc_date is None:
            result.append(r)   # unparseable → include
            continue
        if lo is not None and doc_date < lo:
            continue
        if hi is not None and doc_date >= hi:
            continue
        result.append(r)
    return result


def count_records(user_id: str, date_from: str | None = None, date_to: str | None = None) -> int:
    """Return the number of (non-deleted) documents for the tenant.

    When date_from/date_to are supplied we load the docDate column only and
    filter in Python — because OCR dates arrive in many formats that cannot be
    compared with a SQL text predicate reliably.
    """
    if date_from or date_to:
        # Lightweight query: fetch only documentId + docDate for filtering
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT "documentId","docDate" FROM "FinancialDocument" '
                'WHERE "tenantId"=%s AND "deletedAt" IS NULL',
                (str(user_id),),
            )
            rows = cur.fetchall() or []
        filtered = filter_records_by_doc_date(
            [{"documentId": (r.get("documentId") if isinstance(r, dict) else r[0]),
              "docDate":    (r.get("docDate")    if isinstance(r, dict) else r[1])}
             for r in rows],
            date_from, date_to,
        )
        return len(filtered)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT COUNT(*) AS n FROM "FinancialDocument" WHERE "tenantId"=%s AND "deletedAt" IS NULL',
            (str(user_id),),
        )
        row = cur.fetchone()
    return int((row or {}).get("n", 0))


def load_records(
    user_id: str = None,
    document_id: str = None,
    limit: int | None = None,
    offset: int = 0,
    date_from: str | None = None,
    date_to: str | None = None,
):
    where = ['"deletedAt" IS NULL']
    params: list = []
    if user_id is not None:
        where.append('"tenantId" = %s')
        params.append(str(user_id))
    if document_id is not None:
        where.append('"documentId" = %s')
        params.append(str(document_id))
    _created_at_clause(date_from, date_to, where, params)

    sql = f'SELECT * FROM "FinancialDocument" WHERE {" AND ".join(where)} ORDER BY "createdAt" DESC'
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        params += [limit, offset]

    def _run():
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            docs = cur.fetchall()
            if not docs:
                return []
            doc_ids = [d["id"] if isinstance(d, dict) else d[0] for d in docs]
            cur.execute(
                'SELECT * FROM "LineItem" WHERE "documentRef" = ANY(%s) ORDER BY "lineNo"',
                (doc_ids,),
            )
            items_by_doc = {}
            for li in cur.fetchall():
                items_by_doc.setdefault(li["documentRef"], []).append(_li_to_item(li))

        return [_row_to_record(d, items_by_doc.get(d["id"], [])) for d in docs]

    # Read is idempotent — retry once if the pooled connection was aborted mid-query.
    return run_with_retry(_run)


def load_main_dataset(user_id: str = None) -> pd.DataFrame:
    records = load_records(user_id=user_id)
    if not records:
        return pd.DataFrame(columns=DATASET_COLUMNS)
    df = pd.DataFrame(records)
    for col in DATASET_COLUMNS:
        if col not in df.columns:
            df[col] = "NULL"
    return df[DATASET_COLUMNS]


def load_all_records(user_id: str = None):
    return [parse_record_for_output(r) for r in load_records(user_id=user_id)]


def get_record_by_id_for_user(user_id: str, document_id: str):
    records = load_records(user_id=user_id, document_id=document_id)
    return parse_record_for_output(records[0]) if records else None


def generate_document_id(document_type: str) -> str:
    prefix = get_prefix_for_document_type(document_type)
    offset = len(prefix) + 1  # 1-based SUBSTRING offset for PostgreSQL
    # NOTE: deliberately does NOT filter "deletedAt IS NULL". Soft-deleted rows
    # still physically occupy the (tenantId, documentId) unique key, so the next
    # number must account for them — otherwise deleting the highest doc and
    # re-uploading regenerates the same id and the INSERT hits a UniqueViolation.
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT MAX(CAST(SUBSTRING("documentId" FROM %s) AS INTEGER)) '
            'FROM "FinancialDocument" '
            'WHERE "documentId" LIKE %s '
            "AND SUBSTRING(\"documentId\" FROM %s) ~ '^[0-9]+$'",
            (offset, f"{prefix}%", offset),
        )
        row = cur.fetchone()
    max_num = (row[0] if isinstance(row, (list, tuple)) else row.get(list(row.keys())[0])) if row else None
    return f"{prefix}{(max_num or 0) + 1}"


def find_duplicate_record(data: dict, user_id: str):
    records = load_records(user_id=user_id)
    if not records:
        return None
    new_record = normalize_record(data, user_id=user_id, force_generate_document_id=False)
    for existing in records:
        if is_exact_duplicate(existing, new_record):
            return existing
    return None


# ──────────────────────────── writes ────────────────────────────

def _insert_line_items(cur, doc_pk: str, tenant_id, currency, items: list):
    for idx, item in enumerate(items, start=1):
        cur.execute(
            'INSERT INTO "LineItem" '
            '("id","tenantId","documentRef","lineNo","description","qty","unitPrice","total","currency") '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (
                new_id("li"),
                str(tenant_id),
                doc_pk,
                idx,
                (item.get("description") or "").strip() or None,
                _to_db_money(item.get("quantity")),
                _to_db_money(item.get("unit_price")),
                _to_db_money(item.get("line_total")),
                _to_db_text(currency),
            ),
        )


_COLUMN_CACHE: set | None = None


def invalidate_column_cache():
    global _COLUMN_CACHE
    _COLUMN_CACHE = None


def _get_existing_columns(conn) -> set:
    """Return the column names that actually exist in FinancialDocument.
    Cached per-process; call invalidate_column_cache() after ALTER TABLE migrations."""
    global _COLUMN_CACHE
    if _COLUMN_CACHE is None:
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'FinancialDocument'"""
            )
            _COLUMN_CACHE = {row["column_name"] for row in (cur.fetchall() or [])}
        except Exception:
            _COLUMN_CACHE = set()
    return _COLUMN_CACHE


def upsert_confirmed_record(data: dict, user_id: str):
    # Wrapped in run_with_retry so a connection aborted mid-write (Supabase pooler
    # SSL EOF) recovers on a fresh connection. Idempotent: the duplicate check runs
    # on every attempt, so a retry after a committed-but-aborted insert returns
    # "duplicate_exists" instead of double-inserting.
    def _run():
        duplicate = find_duplicate_record(data, user_id=user_id)
        if duplicate:
            return {"action": "duplicate_exists", "record": parse_record_for_output(duplicate)}

        record = normalize_record(data, user_id=user_id, force_generate_document_id=True)
        doc_pk = new_id("fd")

        all_values: dict = {"id": doc_pk}
        for rec_key, db_col in RECORD_TO_DB.items():
            all_values[db_col] = _record_to_db_value(rec_key, record.get(rec_key))

        items = normalize_items(json.loads(record.get("items_json", "[]") or "[]"))

        with get_conn() as conn:
            cur = conn.cursor()

            # Only include columns that actually exist in the DB.
            # This prevents "column X does not exist" errors when new columns are
            # added to RECORD_TO_DB before the Supabase migration is run.
            existing_cols = _get_existing_columns(conn)
            if existing_cols:
                db_values = {k: v for k, v in all_values.items()
                             if k == "id" or k in existing_cols}
            else:
                db_values = all_values  # fallback: try full insert

            columns = list(db_values.keys())
            col_sql = ", ".join(f'"{c}"' for c in columns) + ', "updatedAt"'
            placeholders = ", ".join(["%s"] * len(columns)) + ", NOW()"
            sql = f'INSERT INTO "FinancialDocument" ({col_sql}) VALUES ({placeholders})'

            cur.execute(sql, list(db_values.values()))
            _insert_line_items(cur, doc_pk, record["user_id"], record.get("currency"), items)

        return {"action": "inserted", "record": parse_record_for_output(record)}

    return run_with_retry(_run)


def update_record_for_user(user_id: str, document_id: str, updates: dict):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            'SELECT * FROM "FinancialDocument" '
            'WHERE "tenantId" = %s AND "documentId" = %s AND "deletedAt" IS NULL',
            (str(user_id), str(document_id)),
        )
        row = cur.fetchone()
        if not row:
            return None
        doc_pk = row["id"]

        cur.execute(
            'SELECT * FROM "LineItem" WHERE "documentRef" = %s ORDER BY "lineNo"',
            (doc_pk,),
        )
        existing_items = [_li_to_item(li) for li in cur.fetchall()]
        existing_record = _row_to_record(row, existing_items)

        try:
            existing_structured = json.loads(existing_record.get("structured_json", "{}"))
            if not isinstance(existing_structured, dict):
                existing_structured = {}
        except Exception:
            existing_structured = {}

        merged_structured = dict(existing_structured)
        merged_structured.update(updates)

        merged_record = dict(existing_record)
        new_items = None
        for key, value in updates.items():
            if key == "items":
                new_items = normalize_items(value)
                merged_record["items_json"] = json.dumps(new_items, ensure_ascii=False).replace("\n", " ")
            elif key in DATASET_COLUMNS:
                merged_record[key] = value

        merged_record["structured_json"] = json.dumps(merged_structured, ensure_ascii=False).replace("\n", " ")

        existing_cols = _get_existing_columns(conn)
        set_cols = []
        set_vals = []
        for rec_key, db_col in RECORD_TO_DB.items():
            if existing_cols and db_col not in existing_cols:
                continue  # skip columns that don't exist in the DB yet
            set_cols.append(f'"{db_col}" = %s')
            set_vals.append(_record_to_db_value(rec_key, merged_record.get(rec_key)))
        set_sql = ", ".join(set_cols) + ', "updatedAt" = NOW()'
        cur.execute(
            f'UPDATE "FinancialDocument" SET {set_sql} WHERE "id" = %s',
            set_vals + [doc_pk],
        )

        if new_items is not None:
            cur.execute('DELETE FROM "LineItem" WHERE "documentRef" = %s', (doc_pk,))
            _insert_line_items(cur, doc_pk, user_id, merged_record.get("currency"), new_items)

    return parse_record_for_output(merged_record)


def delete_record_for_user(user_id: str, document_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            'UPDATE "FinancialDocument" SET "deletedAt" = NOW(), "updatedAt" = NOW() '
            'WHERE "tenantId" = %s AND "documentId" = %s AND "deletedAt" IS NULL',
            (str(user_id), str(document_id)),
        )
        return cur.rowcount > 0
