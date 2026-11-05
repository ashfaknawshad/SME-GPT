"""Reference numbers for PO -> Delivery Note -> Invoice -> Receipt linking.

Two jobs:

1. Normalise reference numbers so the same number written different ways
   compares equal: "PO-2026-041", "po 2026/041", "P.O. #2026-041" and
   "2026-41" all become the key "202641" (see ref_key / ref_core).

2. Work out, for any stored document, its OWN number and the numbers it
   REFERS TO. New extractions provide doc_number / po_ref / dn_ref /
   invoice_ref directly (migration 012). Older documents only have the
   overloaded order_id plus their OCR text, so derive_references() falls back
   to those -- reading labelled references ("PO No: ...", "Invoice No: ...")
   out of the text with regexes, no LLM calls.

Pure functions only (no I/O), so everything here is unit-testable.
"""
from __future__ import annotations

import re

_PLACEHOLDERS = {"", "NULL", "NONE", "N/A", "NA", "-", "0"}

# Leading type words / prefixes that carry no identity ("PO-2026-041" and
# "2026-041" are the same order number). Longest first.
_TYPE_PREFIXES = [
    "PURCHASEORDER", "DELIVERYNOTE", "DELIVERYORDER", "GOODSRECEIVEDNOTE", "TAXINVOICE",
    "INVOICE", "RECEIPT", "ORDER", "QUOTATION",
    "INV", "REC", "RCT", "RCPT", "GRN", "DO", "DN", "PO", "SO", "OR", "IN", "R",
]
_NOISE_WORDS = ("NUMBER", "NO", "NUM", "REF", "REFERENCE", "#")


def is_placeholder(value) -> bool:
    return str(value or "").strip().upper() in _PLACEHOLDERS


def _alnum(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _strip_leading_zeros(text: str) -> str:
    # "000123" -> "123", but keep a lone "0"; applied per digit run.
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", text)


def ref_key(value) -> str:
    """Canonical form of a reference number for exact comparison.

    Removes punctuation, whitespace, leading noise words ("No.", "#"), a single
    leading document-type prefix (PO / DN / INV / ...) and leading zeros in
    digit runs. Returns "" for empty / placeholder values or keys that end up
    with no digits (a bare word like "PO" identifies nothing).
    """
    if is_placeholder(value):
        return ""
    # Strip leading zeros per printed group BEFORE joining, so "2026-041",
    # "2026/41" and "PO 2026-0041" agree.
    groups = re.split(r"[^A-Za-z0-9]+", str(value).upper())
    text = "".join(re.sub(r"^0+(?=[0-9])", "", g) for g in groups if g)
    for word in _NOISE_WORDS:
        word = _alnum(word)
        if word and text.startswith(word) and len(text) > len(word):
            text = text[len(word):]
    for prefix in _TYPE_PREFIXES:
        if text.startswith(prefix) and len(text) > len(prefix):
            rest = text[len(prefix):]
            for word in _NOISE_WORDS:          # "PONO2026041"
                word = _alnum(word)
                if word and rest.startswith(word) and len(rest) > len(word) and rest[len(word)].isdigit():
                    rest = rest[len(word):]
            text = rest
            break
    text = _strip_leading_zeros(text)
    return text if re.search(r"[0-9]", text) else ""


def ref_core(value) -> str:
    """The distinctive tail of a reference, used for the weaker 'sibling
    numbering' signal: DN-AIESEC-582741 and PO-AIESEC-582741 share the core
    "AIESEC582741". Requires at least 4 digits so short counters ("1", "8",
    "4104") never pair up on their own."""
    key = ref_key(value)
    return key if len(re.sub(r"[^0-9]", "", key)) >= 4 else ""


def refs_match(a, b) -> bool:
    ka, kb = ref_key(a), ref_key(b)
    return bool(ka) and ka == kb


# ── labelled references in OCR text (backfill for older documents) ──────────
_REF_VALUE = r"[:#.\s]*\s*([A-Za-z]{0,6}[-/ ]?[0-9][A-Za-z0-9\-/]{0,24})"
_TEXT_PATTERNS = {
    "po_ref": [
        r"(?<![A-Za-z])(?:P\.?\s?O\.?|PURCHASE\s+ORDER|YOUR\s+ORDER|CUSTOMER\s+ORDER|ORDER\s+REF(?:ERENCE)?)\s*(?:NO\.?|NUMBER|NUM|#|REF)?" + _REF_VALUE,
        r"ඇණවුම්\s*අංකය" + _REF_VALUE,
    ],
    "dn_ref": [
        r"(?<![A-Za-z])(?:D\.?\s?N\.?|DELIVERY\s+NOTE|DELIVERY\s+ORDER|GRN|DISPATCH)\s*(?:NO\.?|NUMBER|NUM|#|REF)?" + _REF_VALUE,
    ],
    "invoice_ref": [
        r"(?<![A-Za-z])(?:AGAINST\s+INVOICE|INVOICE\s+REF(?:ERENCE)?|PAYMENT\s+FOR\s+INVOICE|INV\.?\s*REF)\s*(?:NO\.?|NUMBER|#)?" + _REF_VALUE,
    ],
    "doc_number": [
        r"(?<![A-Za-z])(?:INVOICE|INV|RECEIPT|BILL|DELIVERY\s+NOTE|D\.?N\.?|PURCHASE\s+ORDER|P\.?O\.?)\s*(?:NO\.?|NUMBER|NUM|#)" + _REF_VALUE,
        r"(?:ඉන්වොයිස්|රිසිට්|බිල්)\s*(?:පත්\s*)?අංකය" + _REF_VALUE,
    ],
}


def find_refs_in_text(text: str) -> dict[str, list[str]]:
    """All labelled reference numbers found in OCR text, per field."""
    found: dict[str, list[str]] = {}
    if not text:
        return found
    for field, patterns in _TEXT_PATTERNS.items():
        hits: list[str] = []
        for pat in patterns:
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                val = m.group(1).strip(" -/.")
                if ref_key(val) and val not in hits:
                    hits.append(val)
        if hits:
            found[field] = hits
    return found


def _first(*values) -> str:
    for v in values:
        if not is_placeholder(v):
            return str(v).strip()
    return ""


def derive_references(record: dict) -> dict:
    """{doc_number, po_ref, dn_ref, invoice_ref} for one stored record.

    Priority: the explicit migration-012 fields, then structured_json
    (po_reference etc.), then labelled references in the OCR text, then the
    legacy order_id. The legacy order_id is the document's own number on POs,
    invoices and receipts; on delivery notes extraction stored either the PO it
    delivers against or its own number, so a DN order_id that looks like a PO
    number ("PO-...") is treated as po_ref, anything else as its own number.
    """
    doc_type = str(record.get("document_type") or "").strip().lower()
    structured = record.get("structured_json")
    if isinstance(structured, str):
        try:
            import json
            structured = json.loads(structured)
        except Exception:
            structured = {}
    structured = structured if isinstance(structured, dict) else {}

    text = " ".join(str(record.get(k) or "") for k in ("corrected_text", "raw_text"))
    in_text = find_refs_in_text(text)
    order_id = "" if is_placeholder(record.get("order_id")) else str(record.get("order_id")).strip()

    # The "own number" patterns also match "PO No: ..." on an invoice, so skip
    # values that the text labels as a reference to ANOTHER document type.
    foreign = []
    if doc_type != "po":
        foreign += in_text.get("po_ref", [])
    if doc_type != "dn":
        foreign += in_text.get("dn_ref", [])
    own_candidates = [v for v in in_text.get("doc_number", []) if not any(refs_match(v, f) for f in foreign)]
    own_from_text = own_candidates[0] if own_candidates else ""
    po_from_text = [v for v in in_text.get("po_ref", []) if not refs_match(v, own_from_text)]

    legacy_own, legacy_po = "", ""
    if order_id:
        looks_like_po = re.match(r"^\s*(?:P\.?\s?O\.?|PURCHASE\s*ORDER)(?![A-Za-z])", order_id, flags=re.IGNORECASE)
        if doc_type != "po" and looks_like_po:
            legacy_po = order_id
        else:
            legacy_own = order_id

    refs = {
        "doc_number": _first(record.get("doc_number"), structured.get("doc_number"), own_from_text, legacy_own),
        "po_ref": _first(record.get("po_ref"), structured.get("po_ref"), record.get("po_reference"), structured.get("po_reference"),
                         po_from_text[0] if po_from_text else "", legacy_po),
        "dn_ref": _first(record.get("dn_ref"), structured.get("dn_ref"), (in_text.get("dn_ref") or [""])[0]),
        "invoice_ref": _first(record.get("invoice_ref"), structured.get("invoice_ref"),
                              (in_text.get("invoice_ref") or [""])[0]),
    }
    # A document never "refers to" its own kind: a PO's number IS the order
    # number, a DN's "DN No" is its own number, and an invoice's invoice number
    # is not a payment reference.
    if doc_type == "po":
        refs["po_ref"] = ""
    if doc_type == "dn":
        if not refs["doc_number"] and refs["dn_ref"]:
            refs["doc_number"] = refs["dn_ref"]
        refs["dn_ref"] = ""
    if doc_type == "invoice":
        refs["invoice_ref"] = ""
    if refs["po_ref"] and refs_match(refs["po_ref"], refs["doc_number"]) and doc_type != "po":
        # Same number in both slots: on a DN/invoice that number is the PO it belongs to.
        refs["doc_number"] = ""
    return refs
