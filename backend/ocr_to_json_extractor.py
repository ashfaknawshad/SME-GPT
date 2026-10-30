import json
import os
import re
from llm_correction import clean_ocr_text
from llm_client import call_pipeline_llm

_EXTRACTION_SYSTEM = (
    "You are the most accurate financial document extraction engine for Sri Lankan SME businesses. "
    "You have deep knowledge of Sri Lankan invoices, receipts, purchase orders, delivery notes, "
    "VAT/NBT tax structures, Sinhala and English business documents, and common Sri Lankan company formats. "
    "You output ONLY a single valid JSON object — no markdown fences, no explanation, no prose. "
    "Start your response with { and end with }."
)


def extract_via_llm(prompt: str) -> str:
    """Routes extraction calls through the active cloud LLM (Gemini or DeepSeek).

    Requests JSON output mode (format="json") so the model is constrained to emit
    a single valid JSON object. This eliminates most of the conversational
    preamble / markdown-fence noise that the regex scraping in extract_json_block()
    previously had to recover from, reducing JSON parse failures (IT-28).
    """
    return call_pipeline_llm(prompt, system=_EXTRACTION_SYSTEM, format="json")


def extract_json_block(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("LLM response is not a string.")

    # First try direct object extraction
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    # If no JSON found, fail with clearer message
    raise ValueError(
        "No valid JSON object found in LLM response. "
        "The model likely returned conversational text instead of structured output.\n"
        f"Response preview:\n{text[:1500]}"
    )

def clean_json_string(json_text: str) -> str:
    if not isinstance(json_text, str):
        return ""

    fixed = json_text.strip()
    fixed = fixed.replace("```json", "").replace("```", "").strip()
    fixed = fixed.replace("“", '"').replace("”", '"')
    fixed = fixed.replace("‘", "'").replace("’", "'")
    fixed = re.sub(r"\bNone\b", "null", fixed)
    fixed = re.sub(r"\bTrue\b", "true", fixed)
    fixed = re.sub(r"\bFalse\b", "false", fixed)
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)

    return fixed

def normalize_ocr_money(value):
    if value is None:
        return "NULL"

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return "NULL"

    text = text.replace(",", "").replace("Rs.", "").replace("Rs", "").replace("LKR", "").strip()

    # OCR receipt fix: ".300" on receipt totals is often a broken OCR read of "1300".
    # Only applies to exact 3-digit decimal strings (e.g. ".300", ".500").
    if re.fullmatch(r"\.\d{3}", text):
        corrected = int("1" + text[1:])
        print(f"[normalize_ocr_money] OCR receipt fix applied: {text!r} → {corrected}", flush=True)
        return corrected

    try:
        return float(text) if "." in text else int(text)
    except Exception as e:
        print(f"[normalize_ocr_money] parse error for value={value!r}: {e}", flush=True)
        return "NULL"

def normalize_number(value):
    if value is None:
        return "NULL"

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).replace(",", "").replace("Rs.", "").replace("Rs", "").replace("LKR", "").strip()

    if text == "":
        return "NULL"

    try:
        return float(text) if "." in text else int(text)
    except Exception as e:
        print(f"[normalize_number] parse error for value={value!r}: {e}", flush=True)
        return "NULL"


def detect_language(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return "unknown"

    has_sinhala = bool(re.search(r"[඀-෿]", text))
    has_english = bool(re.search(r"[A-Za-z]", text))

    if has_sinhala and has_english:
        return "si-en"
    if has_sinhala:
        return "si"
    if has_english:
        return "en"

    return "unknown"


def normalize_items(items):
    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue

        result.append({
            "description": str(item.get("description", "")).strip(),
            "quantity": normalize_number(item.get("quantity")),
            "unit_price": normalize_number(item.get("unit_price")),
            "line_total": normalize_number(item.get("line_total")),
        })

    return result

def detect_document_type_from_text(text: str) -> str:
    if not isinstance(text, str):
        return "unknown"

    lower = text.lower()

    if "invoice" in lower:
        return "invoice"

    if "purchase order" in lower or re.search(r"\bpo\b", lower):
        return "po"

    if "delivery note" in lower or re.search(r"\bdn\b", lower):
        return "dn"

    receipt_signals = 0

    if "cash return" in lower:
        receipt_signals += 2
    if "total" in lower:
        receipt_signals += 1
    if "date" in lower:
        receipt_signals += 1
    if "order id" in lower:
        receipt_signals += 1
    if re.search(r"^\s*\d+\s+", text, flags=re.MULTILINE):
        receipt_signals += 1

    if receipt_signals >= 3:
        return "receipt"

    return "unknown"

def infer_company_name_from_text(source_text: str, current_company: str) -> str:
    current_company = str(current_company or "").strip()
    lines = [ln.strip() for ln in str(source_text or "").splitlines() if ln.strip()]

    business_keywords = [
        "සැලෝන්", "salon", "shop", "stores", "store",
        "pharmacy", "hotel", "restaurant", "mart", "traders"
    ]

    top_lines = lines[:8]

    # First try current parsed value if it already looks like a business
    if current_company:
        lower_current = current_company.lower()
        if any(keyword in lower_current for keyword in business_keywords):
            return current_company

    # Then prefer top OCR lines that look like business names
    for line in top_lines:
        lower = line.lower()
        if any(keyword in lower for keyword in business_keywords):
            return line

    # If first 2-3 top lines look like a stacked business name, join them
    if len(top_lines) >= 2:
        joined = " ".join(top_lines[:2]).strip()
        if joined:
            return joined

    return current_company if current_company else "Customer"

def normalize_supplier_name(value: str, flow_type: str = "", document_type: str = "") -> str:
    text = str(value or "").strip()
    if not text or text.upper() == "NULL" or text.lower() in ("unknown", ""):
        # Smart default based on context
        if document_type in ("po", "dn") or flow_type in ("payable", "cash_outflow"):
            return "Supplier"
        if flow_type in ("receivable", "cash_inflow"):
            return "Customer"
        return "Supplier"
    return text


def normalize_root_fields(parsed: dict, source_text: str) -> dict:
    if not isinstance(parsed, dict):
        return {}

    language = detect_language(source_text)

    document_type = str(parsed.get("document_type", "unknown")).strip().lower() or "unknown"

    if document_type == "unknown":
        document_type = detect_document_type_from_text(source_text)
    if document_type in {"purchase order", "purchase_order"}:
        document_type = "po"
    if document_type in {"delivery note", "delivery_note"}:
        document_type = "dn"

    flow_type = str(parsed.get("flow_type", "unknown")).strip().lower().replace(" ", "_") or "unknown"
    # Normalise legacy / misspelled values
    _flow_aliases = {
        "expense": "cash_outflow", "expence": "cash_outflow",
        "income": "cash_inflow", "revenue": "cash_inflow",
        "cash outflow": "cash_outflow", "cash inflow": "cash_inflow",
    }
    flow_type = _flow_aliases.get(flow_type, flow_type)

    # DN — delivery notes have no financial value; zero all amounts and lock flow
    if document_type == "dn":
        flow_type = "expense"          # stored for DB consistency, hidden in UI
        parsed["raw_total_amount"]  = ""
        parsed["final_total_amount"] = ""
        parsed["payable_amount"]    = ""
        parsed["cash_return"]       = ""
        parsed["paid_status"]       = "NULL"
        # Strip prices from items — only description + qty matter on a DN
        for item in parsed.get("items", []):
            item["unit_price"] = ""
            item["line_total"] = ""

    # PO — always payable; no cash return, no received status
    if document_type == "po":
        flow_type = "payable"
        parsed["cash_return"]       = ""
        parsed["received_status"]   = "NULL"

    # ── Auto-set paid/received status from flow_type ──────────────────────────
    # These express what has already happened at the time of the document.
    # Payable/receivable are OUTSTANDING — their status is set when settled later.
    _status_map = {
        "cash_outflow": ("NULL",         "paid"),
        "cash_inflow":  ("received",     "NULL"),
        "payable":      ("NULL",         "not_paid"),
        "receivable":   ("not_received", "NULL"),
        "expense":      ("NULL",         "paid"),   # DN uses expense
    }
    if flow_type in _status_map and document_type != "dn":  # DN already handled above
        _rec, _paid = _status_map[flow_type]
        parsed.setdefault("received_status", _rec)
        parsed.setdefault("paid_status",     _paid)
        # Override only if the LLM returned empty/NULL
        if str(parsed.get("received_status", "")).strip() in ("", "NULL", "null"):
            parsed["received_status"] = _rec
        if str(parsed.get("paid_status", "")).strip() in ("", "NULL", "null"):
            parsed["paid_status"] = _paid

    # ── Default date to today if missing ─────────────────────────────────────
    from datetime import date as _today
    _date_val = str(parsed.get("date", "")).strip()
    if not _date_val or _date_val.upper() in ("", "NULL", "NONE"):
        parsed["date"] = _today.today().strftime("%d/%m/%Y")

    # ── Extracted company name from document ─────────────────────────────────
    # company_name from OCR = the ISSUING company on the document.
    # In /confirm-save this will be overridden with the user's registered company.
    # supplier_name = the OTHER party.
    # If supplier_name is empty but company_name was extracted, move company_name
    # to supplier_name (it's the OTHER party's name the document was issued by).
    _ocr_company  = infer_company_name_from_text(source_text, str(parsed.get("company_name", "")).strip())
    _ocr_supplier = str(parsed.get("supplier_name", "")).strip()

    if not _ocr_supplier or _ocr_supplier.upper() in ("NULL", "NONE"):
        # Use the extracted company name as the OTHER party's name
        _ocr_supplier = _ocr_company if _ocr_company else ""

    _supplier_final = normalize_supplier_name(_ocr_supplier, flow_type, document_type)

    return {
        "document_id": str(parsed.get("document_id", "")).strip(),
        "document_type": document_type,
        "order_id": str(parsed.get("order_id", "")).strip(),
        "flow_type": flow_type,
        "company_name": _ocr_company,   # will be overridden in /confirm-save with user's company
        "supplier_name": _supplier_final,
        "date": str(parsed.get("date", "")).strip(),
        "currency": str(parsed.get("currency", "")).strip(),
        "raw_total_amount": normalize_ocr_money(parsed.get("raw_total_amount")),
        "final_total_amount": normalize_ocr_money(parsed.get("final_total_amount")),
        "payable_amount": normalize_ocr_money(parsed.get("payable_amount")),
        "cash_return": normalize_number(parsed.get("cash_return")),
        "tax_amount": normalize_ocr_money(parsed.get("tax_amount")) if parsed.get("tax_amount") not in (None, "", "NULL") else "NULL",
        "tax_rate": normalize_number(parsed.get("tax_rate")) if parsed.get("tax_rate") not in (None, "", "NULL") else "NULL",
        "received_status": str(parsed.get("received_status", "")).strip(),
        "paid_status": str(parsed.get("paid_status", "")).strip(),
        "language": language,
        "items": normalize_items(parsed.get("items", [])),
        # Iteration 10 — PO/DN workflow fields
        "due_date": str(parsed.get("due_date", "")).strip(),
        "delivery_date": str(parsed.get("delivery_date", "")).strip(),
        "approved_by": str(parsed.get("approved_by", "")).strip(),
        "proof_of_delivery": parsed.get("proof_of_delivery", None),
        "signed": parsed.get("signed", None),
        # Iteration 11 — contact/location fields
        "supplier_city": str(parsed.get("supplier_city", "")).strip(),
        "supplier_phone": str(parsed.get("supplier_phone", "")).strip(),
        "supplier_email": str(parsed.get("supplier_email", "")).strip(),
        "company_city": str(parsed.get("company_city", "")).strip(),
        "company_phone": str(parsed.get("company_phone", "")).strip(),
        "company_email": str(parsed.get("company_email", "")).strip(),
    }

def retry_extract_json_only(raw_text: str) -> str:
    retry_prompt = f"""
Return ONLY one valid JSON object — no markdown, no explanations, no notes.

Extract ALL line items. For each item row extract:
- description (item name)
- quantity (Qty/QTY/Nos/Pcs/Units/ප්‍රමාණය)
- unit_price (Rate/Price/Unit Rate/Unit Price/ඒකක මිල)
- line_total (Amount/Total/මුළු මිල/රු.)
A column headed මුළු මිල is the line total, never the unit price.

Use "" for any missing field. Do NOT skip item rows.
Default currency to "LKR" if not stated.

{{
  "document_id": "",
  "document_type": "unknown",
  "order_id": "",
  "flow_type": "unknown",
  "company_name": "",
  "supplier_name": "",
  "date": "",
  "currency": "LKR",
  "raw_total_amount": "",
  "final_total_amount": "",
  "payable_amount": "",
  "cash_return": "",
  "tax_amount": "",
  "tax_rate": "",
  "received_status": "",
  "paid_status": "",
  "items": [
    {{
      "description": "",
      "quantity": "",
      "unit_price": "",
      "line_total": ""
    }}
  ]
}}

OCR text:
{raw_text}
""".strip()

    return extract_via_llm(retry_prompt)


_TYPE_HINTS = {
    "invoice": (
        "invoice/bill/tax invoice — issued by a company to a client for services/goods provided. "
        "flow_type: 'receivable' (client owes us) or 'payable' (we owe supplier)."
    ),
    "receipt": (
        "cash receipt/POS bill/shop bill — records money already exchanged. "
        "flow_type: 'cash_inflow' (money received) or 'cash_outflow' (money paid out)."
    ),
    "po": (
        "purchase order — authorises a supplier to deliver goods at agreed prices. "
        "flow_type: always 'payable'. No cash has changed hands yet."
    ),
    "dn": (
        "delivery note/goods received note — records physical delivery of goods. "
        "No financial amounts. flow_type: 'expense' (stored for DB only)."
    ),
}


_FEW_SHOT_EXAMPLES = '''
=== EXAMPLE 1: English Tax Invoice (we are the buyer — payable) ===
OCR text:
TAX INVOICE
Invoice No: INV-2025-0891
Date: 12/03/2025
From: Colombo Office Supplies (Pvt) Ltd
       No. 45, Galle Road, Colombo 03
       Tel: 0112-345678
To: Tech Solutions Lanka (Pvt) Ltd
    No. 78, Union Place, Colombo 02

Description          Qty   Unit Price    Amount
A4 Paper (500 sheets)  10      350.00   3,500.00
Whiteboard Markers     5       180.00     900.00
Stapler (Heavy Duty)   2       750.00   1,500.00

Subtotal:            5,900.00
VAT (18%):           1,062.00
Total:               6,962.00
Due Date: 12/04/2025

Expected output:
{"document_id":"","document_type":"invoice","order_id":"INV-2025-0891","flow_type":"payable","company_name":"Colombo Office Supplies (Pvt) Ltd","supplier_name":"Tech Solutions Lanka (Pvt) Ltd","date":"12/03/2025","currency":"LKR","raw_total_amount":"6962.00","final_total_amount":"6962.00","payable_amount":"6962.00","cash_return":"","tax_amount":"1062.00","tax_rate":"18","received_status":"","paid_status":"not_paid","due_date":"12/04/2025","delivery_date":"","approved_by":"","proof_of_delivery":null,"signed":null,"supplier_city":"Colombo 02","supplier_phone":"","supplier_email":"","company_city":"Colombo 03","company_phone":"0112-345678","company_email":"","items":[{"description":"A4 Paper (500 sheets)","quantity":"10","unit_price":"350.00","line_total":"3500.00"},{"description":"Whiteboard Markers","quantity":"5","unit_price":"180.00","line_total":"900.00"},{"description":"Stapler (Heavy Duty)","quantity":"2","unit_price":"750.00","line_total":"1500.00"}]}

=== EXAMPLE 2: Sinhala Receipt (we paid cash — cash_outflow) ===
OCR text:
ශ්‍රී ලංකා සුපිරි වෙළෙඳසැල
කොළඹ 07
දු.අ: 0112-678901

ලදුපත අංකය: R-4521
දිනය: 25/05/2025
කේෂියර්: නිලූකා

විස්තරය         ප්‍රමාණය  ඒකක මිල  එකතුව
සහල් (1kg)          2      180      360
කිරිපිටි (400g)      1      420      420
පාන් (සම්පූර්ණ)      1       95       95

එකතුව:  875.00
ලැබූ:  1000.00
ශේෂය:   125.00

Expected output:
{"document_id":"","document_type":"receipt","order_id":"R-4521","flow_type":"cash_outflow","company_name":"ශ්‍රී ලංකා සුපිරි වෙළෙඳසැල","supplier_name":"","date":"25/05/2025","currency":"LKR","raw_total_amount":"875.00","final_total_amount":"875.00","payable_amount":"875.00","cash_return":"125.00","tax_amount":"","tax_rate":"","received_status":"","paid_status":"","due_date":"","delivery_date":"","approved_by":"","proof_of_delivery":null,"signed":null,"supplier_city":"කොළඹ 07","supplier_phone":"0112-678901","supplier_email":"","company_city":"","company_phone":"","company_email":"","items":[{"description":"සහල් (1kg)","quantity":"2","unit_price":"180","line_total":"360"},{"description":"කිරිපිටි (400g)","quantity":"1","unit_price":"420","line_total":"420"},{"description":"පාන් (සම්පූර්ණ)","quantity":"1","unit_price":"95","line_total":"95"}]}

=== EXAMPLE 3: Purchase Order (we are ordering — payable) ===
OCR text:
PURCHASE ORDER
PO Number: PO-2025-0234
Date: 01/06/2025
To (Supplier): Print Masters Lanka
               123 Baseline Road, Colombo 09
From (Buyer): AIESEC Sri Lanka
              341/A, Kotte Road, Nugegoda

Item                    Units  Rate/Unit    Amount
Event Banners 6x4ft       10    3,500.00   35,000.00
Brochures A5 (500 pcs)     5    4,200.00   21,000.00

Total: LKR 56,000.00
Approved by: Kasun Perera
Delivery by: 15/06/2025

Expected output:
{"document_id":"","document_type":"po","order_id":"PO-2025-0234","flow_type":"payable","company_name":"AIESEC Sri Lanka","supplier_name":"Print Masters Lanka","date":"01/06/2025","currency":"LKR","raw_total_amount":"56000.00","final_total_amount":"56000.00","payable_amount":"56000.00","cash_return":"","tax_amount":"","tax_rate":"","received_status":"","paid_status":"not_paid","due_date":"","delivery_date":"15/06/2025","approved_by":"Kasun Perera","proof_of_delivery":null,"signed":null,"supplier_city":"Colombo 09","supplier_phone":"","supplier_email":"","company_city":"Nugegoda","company_phone":"","company_email":"","items":[{"description":"Event Banners 6x4ft","quantity":"10","unit_price":"3500.00","line_total":"35000.00"},{"description":"Brochures A5 (500 pcs)","quantity":"5","unit_price":"4200.00","line_total":"21000.00"}]}
'''


def _build_extraction_prompt(cleaned_text: str, doc_type_hint: str = "") -> str:
    """Build a comprehensive few-shot extraction prompt.

    Includes 3 worked examples covering invoice/receipt/PO, Sinhala and English,
    payable/cash_outflow/receivable flow types, and all IT-10/IT-11 fields.
    doc_type_hint narrows the type section when the doc type is already known.
    """
    if doc_type_hint and doc_type_hint in _TYPE_HINTS:
        type_section = (
            f'This document is a {_TYPE_HINTS[doc_type_hint]}\n'
            f'Set document_type="{doc_type_hint}".'
        )
    else:
        type_section = (
            'DOCUMENT TYPE — pick exactly one:\n'
            '  "invoice"  → document header says Invoice / Bill / Tax Invoice / කුවිතාන්සිය\n'
            '  "receipt"  → POS receipt / cash bill / shop receipt / ලදුපත\n'
            '  "po"       → Purchase Order / PO / ඇණවුම\n'
            '  "dn"       → Delivery Note / Goods Received Note / භාර දීමේ සටහන\n'
            '  "unknown"  → none of the above'
        )

    return f"""Extract ALL fields from the financial document below. Return ONLY one valid JSON object.

ABSOLUTE RULES:
1. Copy field values EXACTLY as they appear — never rephrase or translate
2. Preserve ALL Sinhala Unicode characters exactly — do NOT romanise or translate Sinhala
3. Never compute or invent numbers — use "" for any missing field
4. Default currency is LKR unless another currency is printed

PARTY DETECTION (most common extraction error):
- company_name  = the business at the TOP of the document — the ISSUER (the shop, the supplier, the company sending this document)
- supplier_name = the OTHER party — the buyer, client, recipient, or counterparty
- For a shop receipt: company_name = shop name, supplier_name = "" (customer usually not named)
- For an invoice FROM us TO a client: company_name = our company, supplier_name = client
- For an invoice FROM a supplier TO us: company_name = supplier, supplier_name = our company

FLOW TYPE:
- "payable"      → we owe the supplier (we are buying, not yet paid)
- "receivable"   → client owes us (we issued the invoice, not yet received payment)
- "cash_outflow" → we already paid (shop receipt, petty cash, expense already settled)
- "cash_inflow"  → we already received payment from a client
- "unknown"      → genuinely unclear

{type_section}

LINE ITEMS — extract EVERY row:
- quantity labels: Qty / QTY / Nos / No. / Pcs / Units / ප්‍රමාණය
- unit_price labels: Rate / Unit Rate / Price / U/Price / ඒකක මිල
- line_total labels: Amount / Total / Line Total / Sub / මුළු මිල / මුළු මුදල / රු.
- මුළු මිල ("total price") is the LINE TOTAL column, NOT the unit price. When a row
  has both a unit price and a total, line_total = quantity × unit_price
  (e.g. qty 2, ඒකක මිල 220.00, මුළු මිල 440.00 → unit_price "220.00", line_total "440.00")
- If a receipt has only price and total (no qty column): set quantity="1"
- Preserve Sinhala item names exactly

TAX — only extract what is EXPLICITLY printed:
- tax_amount: actual rupee amount (e.g. "VAT: 1,062.00" → "1062.00")
- tax_rate: percentage printed (e.g. "VAT 18%" → "18")
- Do NOT calculate tax from subtotal

WORKFLOW FIELDS (extract only if explicitly printed, use "" or null otherwise):
- due_date: payment due date (e.g. "Due Date: 30/04/2025")
- delivery_date: expected delivery date (e.g. "Delivery by: 15/06/2025")
- approved_by: name of approver (e.g. "Approved by: Kasun Perera")
- proof_of_delivery: true if there is a "Received By" / signature section, false if signature box is blank, null if unknown
- signed: true if a signature is present, false if blank, null if unknown

CONTACT FIELDS (extract only if visible in the document):
- company_city: city of the issuer (top of document)
- company_phone: phone of the issuer
- company_email: email of the issuer
- supplier_city: city of the counterparty
- supplier_phone: phone of the counterparty
- supplier_email: email of the counterparty

STATUS FIELDS:
- paid_status: set "not_paid" for invoice/PO we owe; "" for receipts; "" for receivables
- received_status: set "not_received" for receivable invoices; "" otherwise

{_FEW_SHOT_EXAMPLES}

=== NOW EXTRACT FROM THE DOCUMENT BELOW ===
OCR text:
{cleaned_text}

Return ONLY the JSON object matching this structure (no extra text):
{{
  "document_id": "",
  "document_type": "unknown",
  "order_id": "",
  "flow_type": "unknown",
  "company_name": "",
  "supplier_name": "",
  "date": "",
  "currency": "LKR",
  "raw_total_amount": "",
  "final_total_amount": "",
  "payable_amount": "",
  "cash_return": "",
  "tax_amount": "",
  "tax_rate": "",
  "received_status": "",
  "paid_status": "",
  "due_date": "",
  "delivery_date": "",
  "approved_by": "",
  "proof_of_delivery": null,
  "signed": null,
  "supplier_city": "",
  "supplier_phone": "",
  "supplier_email": "",
  "company_city": "",
  "company_phone": "",
  "company_email": "",
  "items": [
    {{"description": "", "quantity": "", "unit_price": "", "line_total": ""}}
  ]
}}"""


def extract_structured_json_from_text(raw_text: str, doc_type_hint: str = "") -> dict:
    cleaned_text = clean_ocr_text(raw_text)

    prompt = _build_extraction_prompt(cleaned_text, doc_type_hint)

    llm_response = extract_via_llm(prompt)

    try:
        json_block = extract_json_block(llm_response)
    except ValueError:
        llm_response = retry_extract_json_only(cleaned_text)
        json_block = extract_json_block(llm_response)

    cleaned_json_block = clean_json_string(json_block)
    try:
        parsed = json.loads(cleaned_json_block)
    except json.JSONDecodeError as e:
        raise Exception(
            "LLM returned invalid JSON during extraction.\n"
            f"JSON error: {e}\n"
            f"Problematic JSON preview:\n{cleaned_json_block[:1500]}"
        )

    return normalize_root_fields(parsed, cleaned_text)
