"""PO -> Delivery Note -> Invoice -> Receipt linking and three-way matching.

Hermetic: pure functions only (references.py, document_links scoring,
order_matching) -- no database, no LLM.
"""
from __future__ import annotations

import json

import pytest

from document_links import (
    AUTO_THRESHOLD,
    SUGGEST_THRESHOLD,
    chain_component,
    item_name,
    item_similarity,
    item_tokens,
    prepare,
    propose_links,
    score_pair,
)
from order_matching import compute_order
from references import derive_references, ref_core, ref_key


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def rec(doc_id, doc_type, *, supplier="Lanka Cement Distributors", total=0, date="2026-10-10",
        items=(), flow="payable", **extra):
    return {
        "document_id": doc_id, "document_type": doc_type, "supplier_name": supplier,
        "final_total_amount": str(total), "date": date, "flow_type": flow, "currency": "LKR",
        "items_json": json.dumps([
            {"description": d, "quantity": str(q), "unit_price": str(p), "line_total": str(q * p)}
            for d, q, p in items
        ]),
        **extra,
    }


def link(frm, to, link_type, status="auto"):
    return {"from": frm, "to": to, "link_type": link_type, "status": status}


# ---------------------------------------------------------------------------
# reference numbers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("PO-2026-041", "po 2026/041"),
    ("PO-2026-041", "P.O. #2026-041"),
    ("PO-2026-041", "2026-41"),
    ("PO No: 2026-0041", "PO-2026-041"),
    ("INV 2026-0744", "INV-2026-744"),
])
def test_same_reference_written_differently(a, b):
    assert ref_key(a) and ref_key(a) == ref_key(b)


@pytest.mark.parametrize("a,b", [("PO-2026-041", "PO-2026-042"), ("2026-041", "2027-041"), ("IN5", "IN50")])
def test_different_references_do_not_match(a, b):
    assert ref_key(a) != ref_key(b)


def test_placeholders_and_bare_words_have_no_key():
    for v in ("", "NULL", "none", "N/A", "-", "PO", "Invoice"):
        assert ref_key(v) == ""


def test_sibling_numbering_core():
    assert ref_core("DN-AIESEC-582741") == ref_core("PO-AIESEC-582741") != ""
    assert ref_core("R5") == "" and ref_core("8") == ""  # short counters never pair up


def test_po_label_inside_a_word_is_not_a_reference():
    refs = derive_references({"document_type": "receipt", "order_id": "R-EXPO-3001",
                              "raw_text": "Receipt R-EXPO-3001 Expo Lanka"})
    assert refs["po_ref"] == ""
    assert refs["doc_number"] == "R-EXPO-3001"


def test_legacy_po_number_on_invoice_is_a_reference_not_its_own_number():
    refs = derive_references({"document_type": "invoice", "order_id": "PO-EXPO-1001"})
    assert refs["po_ref"] == "PO-EXPO-1001" and refs["doc_number"] == ""


def test_invoice_text_listing_po_first_keeps_own_number():
    refs = derive_references({"document_type": "invoice",
                              "raw_text": "Your PO No: PO-2026-041   Invoice No: INV-778"})
    assert refs == {"doc_number": "INV-778", "po_ref": "PO-2026-041", "dn_ref": "", "invoice_ref": ""}


def test_dn_number_is_its_own_number_not_a_dn_reference():
    refs = derive_references({"document_type": "dn", "raw_text": "DELIVERY NOTE  DN No: DN-2026-118  PO No: PO-2026-041"})
    assert refs["doc_number"] == "DN-2026-118" and refs["dn_ref"] == "" and refs["po_ref"] == "PO-2026-041"


def test_explicit_fields_win_over_text():
    refs = derive_references({"document_type": "invoice", "doc_number": "INV-1", "po_ref": "PO-9",
                              "raw_text": "Invoice No INV-2  PO No PO-3"})
    assert refs["doc_number"] == "INV-1" and refs["po_ref"] == "PO-9"


# ---------------------------------------------------------------------------
# item matching (Sinhala, OCR typos, shared sizes)
# ---------------------------------------------------------------------------

def _it(d):
    return {"tokens": item_tokens(d), "name": item_name(d)}


def test_sinhala_ocr_typo_still_matches_the_right_item():
    dn_line = _it("සිති මලු (කි.ග්‍රෑ. 25)")          # OCR typo of සීනි (sugar)
    sugar = _it("සීනි මලු (කි.ග්‍රෑ. 25)")
    rice = _it("සුදු සහල් මලු (කි.ග්‍රෑ. 25)")
    assert item_similarity(dn_line, sugar) > item_similarity(dn_line, rice) + 0.15


def test_shared_size_and_packaging_alone_is_not_a_match():
    assert item_similarity(_it("Sugar 25kg bag"), _it("Rice 25kg bag")) < 0.5


def test_same_product_different_wording_matches():
    assert item_similarity(_it("Portland Cement 50kg"), _it("Portland Cement 50kg bags (Tokyo Super)")) >= 0.5


# ---------------------------------------------------------------------------
# link scoring
# ---------------------------------------------------------------------------

CEMENT = [("Portland Cement 50kg", 100, 2150)]


def test_printed_po_reference_plus_same_supplier_links_automatically():
    po = prepare(rec("PO8", "po", order_id="PO-2026-041", total=215000, items=CEMENT))
    dn = prepare(rec("DN7", "dn", po_ref="PO-2026-041", date="2026-10-12", items=[("Portland Cement 50kg", 60, 0)]))
    score, reasons = score_pair(dn, po)
    assert score >= AUTO_THRESHOLD
    assert any("refers to" in r for r in reasons)


def test_a_different_supplier_blocks_the_link():
    po = prepare(rec("PO8", "po", order_id="PO-2026-041", items=CEMENT))
    dn = prepare(rec("DN9", "dn", supplier="Ruhunu Paint House", po_ref="PO-2026-041"))
    assert score_pair(dn, po)[0] < AUTO_THRESHOLD


def test_no_identity_evidence_means_no_link():
    # Same date window, no numbers, no matching items/amount -> never linked.
    po = prepare(rec("PO1", "po", total=5000, items=[("Bricks", 100, 50)]))
    dn = prepare(rec("DN1", "dn", items=[("Paint", 5, 0)]))
    assert score_pair(dn, po)[0] == 0.0


def test_receipt_matching_invoice_amount_and_supplier_links():
    inv = prepare(rec("IN1", "invoice", order_id="INV-55", total=18500, items=[("Transport", 1, 18500)]))
    r = prepare(rec("R1", "receipt", total=18500, date="2026-10-20", items=[("Transport", 1, 18500)], flow="cash_outflow"))
    assert score_pair(r, inv)[0] >= SUGGEST_THRESHOLD


def test_purchase_never_links_to_a_sale():
    po = prepare(rec("PO8", "po", order_id="PO-2026-041", items=CEMENT))
    inv = prepare(rec("IN5", "invoice", po_ref="PO-2026-041", flow="receivable", items=CEMENT))
    assert score_pair(inv, po)[0] < AUTO_THRESHOLD


def test_tie_between_two_parents_is_only_suggested():
    p1 = prepare(rec("PO1", "po", items=CEMENT, total=215000))
    p2 = prepare(rec("PO2", "po", items=CEMENT, total=215000))
    dn = prepare(rec("DN1", "dn", items=[("Portland Cement 50kg", 100, 0)], total=215000))
    links = [l for l in propose_links([p1, p2, dn]) if l["from"] == "DN1"]
    assert links and all(l["status"] == "suggested" for l in links)


def test_user_decisions_are_respected():
    po = prepare(rec("PO8", "po", order_id="PO-2026-041", items=CEMENT))
    dn = prepare(rec("DN7", "dn", po_ref="PO-2026-041", items=[("Portland Cement 50kg", 60, 0)]))
    assert propose_links([po, dn], {("DN7", "PO8", "po_dn"): "rejected"}) == []
    assert propose_links([po, dn], {("DN7", "PO8", "po_dn"): "confirmed"}) == []  # nothing new to propose


def test_chain_component_uses_only_active_links():
    links = [link("DN7", "PO8", "po_dn"), link("IN21", "PO8", "po_invoice"),
             link("R1", "IN21", "invoice_receipt", status="suggested"), link("X", "Y", "po_dn", status="rejected")]
    assert chain_component("DN7", links) == {"DN7", "PO8", "IN21"}


# ---------------------------------------------------------------------------
# three-way match
# ---------------------------------------------------------------------------

def _po8_order():
    po = prepare(rec("PO8", "po", order_id="PO-2026-041", total=215000, items=CEMENT, po_status="approved"))
    dn = prepare(rec("DN7", "dn", doc_number="DN-2026-118", po_ref="PO-2026-041", date="2026-10-12",
                     dn_status="delivered", items=[("Portland Cement 50kg", 60, 0)]))
    inv = prepare(rec("IN21", "invoice", doc_number="INV-LC-7781", po_ref="PO-2026-041", date="2026-10-14",
                      total=225000, items=[("Portland Cement 50kg", 100, 2250)], paid_status="not_paid"))
    links = [link("DN7", "PO8", "po_dn"), link("IN21", "PO8", "po_invoice"), link("IN21", "DN7", "dn_invoice")]
    return [po, dn, inv], links


def test_partial_delivery_over_billing_and_price_difference():
    docs, links = _po8_order()
    view = compute_order(docs, links)
    line = view["lines"][0]
    assert (line["ordered"], line["delivered"], line["invoiced"]) == (100, 60, 100)
    assert line["delivery_status"] == "partial"
    codes = {i["code"] for i in view["issues"]}
    assert {"partial_delivery", "over_billed", "price_mismatch"} <= codes
    msg = next(i for i in view["issues"] if i["code"] == "partial_delivery")
    assert "60 of 100" in msg["message_en"] and msg["message_si"]
    assert view["delivery_stage"] == "partial"
    assert {"document_id": "PO8", "field": "po_status", "from": "approved", "to": "partially_delivered"} in view["updates"]


def test_full_delivery_marks_po_fulfilled_and_no_false_issues():
    po = prepare(rec("PO4", "po", order_id="PO-2026-501", items=[
        ("සුදු සහල් මලු (කි.ග්‍රෑ. 25)", 40, 4250), ("සීනි මලු (කි.ග්‍රෑ. 25)", 20, 5400)]))
    dn = prepare(rec("DN4", "dn", po_ref="PO-2026-501", dn_status="delivered", items=[
        ("සුදු සහල් මලු (කි.ග්‍රෑ. 25)", 40, 0), ("සිති මලු (කි.ග්‍රෑ. 25)", 20, 0)]))  # OCR typo
    view = compute_order([po, dn], [link("DN4", "PO4", "po_dn")])
    assert view["delivery_stage"] == "full"
    assert not [i for i in view["issues"] if i["code"] in ("over_delivery", "item_not_on_po", "partial_delivery")]
    assert any(u["field"] == "po_status" and u["to"] == "fulfilled" for u in view["updates"])


def test_partial_payment_by_receipt():
    docs, links = _po8_order()
    receipt = prepare(rec("R50", "receipt", total=100000, date="2026-10-20", flow="cash_outflow"))
    view = compute_order(docs + [receipt], links + [link("R50", "IN21", "invoice_receipt")])
    inv = view["invoices"][0]
    assert (inv["status"], inv["paid"], inv["balance"]) == ("partially_paid", 100000, 125000)
    assert any(i["code"] == "unpaid_balance" for i in view["issues"])
    fields = {(u["document_id"], u["field"]): u["to"] for u in view["updates"]}
    assert fields[("IN21", "invoice_status")] == "partially_paid"
    assert fields[("IN21", "paid_status")] == "partial"


def test_manual_and_payment_statuses_are_never_undone():
    docs, links = _po8_order()
    docs[0]["record"]["po_status"] = "cancelled"         # manual terminal PO state
    docs[2]["record"]["paid_status"] = "paid"            # user marked the invoice paid
    docs[2]["record"]["invoice_status"] = "paid"
    view = compute_order(docs, links)
    assert not [u for u in view["updates"] if u["document_id"] == "PO8"]
    assert not [u for u in view["updates"] if u["document_id"] == "IN21"]
    assert view["invoices"][0]["status"] == "paid"
