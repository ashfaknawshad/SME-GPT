"""The dashboard's arithmetic warnings: real mismatches only.

Figures are from three documents that were flagged in production: a delivery
note (DN4, no amounts), a receipt whose items were corrected after extraction
(R50), and an invoice that really doesn't add up (IN15).
"""
import json

import jwt as pyjwt
from fastapi.testclient import TestClient

import app as app_mod

from arithmetic_validator import effective_arithmetic_status, revalidate_after_edit, validate_arithmetic
from dataset_manager import parse_record_for_output


def _line(qty, price):
    return {"description": "x", "quantity": qty, "unit_price": price, "line_total": qty * price}


def _discrepancies(printed, computed):
    return [{"field": f, "extracted": printed, "computed": computed}
            for f in ("raw_total_amount", "final_total_amount", "payable_amount")]


def _saved(doc_type, items, final, discrepancies=(), computed=None, **extra):
    """A record as it comes back from the database."""
    return {
        "document_id": "D1", "document_type": doc_type, "arithmetic_status": "mismatch",
        "raw_total_amount": final, "final_total_amount": final, "payable_amount": final,
        "items_json": json.dumps(items), "structured_json": json.dumps(extra),
        "arithmetic_json": json.dumps({"status": "mismatch", "computed_items_total": computed,
                                       "discrepancies": list(discrepancies)}),
    }


IN15_ITEMS = [_line(1, 32), _line(25, 89), _line(15, 145), _line(10, 420), _line(30, 22.5)]


def test_delivery_note_is_never_flagged():
    items = [{"description": "Rice", "quantity": 40, "unit_price": 0, "line_total": 0}]
    record = _saved("dn", items, 0.0, computed=0.0)
    assert parse_record_for_output(record)["arithmetic_status"] == "not_checked"


def test_delivery_note_with_stray_amounts_is_not_a_mismatch():
    validation = validate_arithmetic({"document_type": "dn", "items": [_line(5, 100)], "final_total_amount": "700"})
    assert validation["status"] == "not_checked"
    assert validation["discrepancies"] == []
    assert validation["recommended"]["final_total_amount"] == 700.0


def test_receipt_with_corrected_items_clears():
    record = _saved("receipt", [_line(100, 300), _line(5, 350)], 31750.0,
                    _discrepancies(31750.0, 31749.0), computed=31749.0)
    assert parse_record_for_output(record)["arithmetic_status"] == "matched"


def test_invoice_that_really_does_not_add_up_stays_flagged():
    record = _saved("invoice", IN15_ITEMS, 7641.34, _discrepancies(7641.34, 9307.0), computed=9307.0,
                    tax_amount=630.94, tax_rate=9.0)
    assert parse_record_for_output(record)["arithmetic_status"] == "mismatch"


def test_real_mismatch_stays_flagged_after_the_total_was_overwritten():
    # Printed 240, items 1,230: the pipeline set the final total to 1,230, which
    # now equals the items. The printed figure is what must be compared.
    record = _saved("receipt", [_line(1, 80), _line(1, 1100), _line(1, 50)], 1230.0,
                    [{"field": "final_total_amount", "extracted": 240.0, "computed": 1230.0}], computed=1230.0)
    assert parse_record_for_output(record)["arithmetic_status"] == "mismatch"


def test_older_vat_invoice_clears_once_its_tax_is_read():
    record = _saved("invoice", [_line(10, 100)], 1180.0, _discrepancies(1180.0, 1000.0), computed=1000.0, tax_amount="180")
    assert parse_record_for_output(record)["arithmetic_status"] == "matched"


def test_other_statuses_are_left_alone():
    assert effective_arithmetic_status({"arithmetic_status": "matched"}) == "matched"
    assert effective_arithmetic_status({"arithmetic_status": "low_confidence"}) == "low_confidence"


def _extracted(**fields):
    return {"document_type": "receipt", "items": [_line(100, 299.99), _line(5, 350)],
            "final_total_amount": 31750.0, **fields}


def test_correcting_items_before_saving_settles_the_warning():
    after = _extracted(items=[_line(100, 300), _line(5, 350)])
    assert revalidate_after_edit(_extracted(), after)["status"] == "matched"


def test_saving_unchanged_keeps_the_original_check():
    assert revalidate_after_edit(_extracted(), _extracted()) is None


def test_saving_with_only_the_format_changed_keeps_the_original_check():
    before = _extracted(tax_amount="NULL", discount_amount=None)
    after = _extracted(final_total_amount="31,750.00", tax_amount="", discount_amount=0)
    assert revalidate_after_edit(before, after) is None


def test_paid_status_edit_keeps_the_original_check():
    assert revalidate_after_edit(_extracted(), {**_extracted(), "paid_status": "paid"}) is None


def test_retyping_to_a_delivery_note_clears_the_warning():
    assert revalidate_after_edit(_extracted(), _extracted(document_type="dn"))["status"] == "not_checked"


def test_an_edit_that_breaks_the_arithmetic_raises_a_warning():
    before = {"document_type": "invoice", "items": [_line(10, 100)], "final_total_amount": 1000.0}
    after = {**before, "final_total_amount": 1500.0}
    assert revalidate_after_edit(before, after)["status"] == "mismatch"


def test_verified_stays_verified_when_read():
    record = _saved("po", [_line(3, 2500), _line(3, 500), _line(5, 450)], 11250.0,
                    _discrepancies(2500.0, 11250.0), computed=11250.0)
    assert parse_record_for_output(record)["arithmetic_status"] == "mismatch"
    assert parse_record_for_output({**record, "arithmetic_status": "verified"})["arithmetic_status"] == "verified"


def test_changing_the_figures_of_a_verified_document_checks_it_again():
    before = {"document_type": "po", "arithmetic_status": "verified", "items": [_line(3, 2500)], "final_total_amount": 7500.0}
    after = {**before, "final_total_amount": 9000.0}
    assert revalidate_after_edit(before, after)["status"] == "mismatch"


# ── POST /documents/{id}/verify-totals ────────────────────────────────────────

client = TestClient(app_mod.app, raise_server_exceptions=False)


def _auth(role="owner"):
    token = pyjwt.encode({"userId": "u1", "role": role}, app_mod.JWT_SECRET, algorithm=app_mod.JWT_ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


def _patch_verify(monkeypatch, status):
    writes = []
    monkeypatch.setattr(app_mod, "get_record_by_id_for_user",
                        lambda user_id, document_id: {"document_id": document_id, "arithmetic_status": status} if status else None)

    def fake_update(user_id, document_id, updates):
        writes.append((user_id, document_id, updates))
        return {"document_id": document_id, **updates}
    monkeypatch.setattr(app_mod, "update_record_for_user", fake_update)
    monkeypatch.setattr(app_mod, "_log_audit_event", lambda *a, **k: None)
    return writes


def test_verify_clears_the_warning_without_touching_any_amount(monkeypatch):
    writes = _patch_verify(monkeypatch, "mismatch")
    res = client.post("/documents/PO1/verify-totals", headers=_auth())
    assert res.status_code == 200
    assert res.json()["document"]["arithmetic_status"] == "verified"
    assert writes == [("u1", "PO1", {"arithmetic_status": "verified"})]


def test_verify_refuses_a_document_with_no_warning(monkeypatch):
    writes = _patch_verify(monkeypatch, "matched")
    assert client.post("/documents/IN1/verify-totals", headers=_auth()).status_code == 400
    assert writes == []


def test_verify_returns_404_for_a_document_outside_the_tenant(monkeypatch):
    writes = _patch_verify(monkeypatch, None)
    assert client.post("/documents/PO1/verify-totals", headers=_auth()).status_code == 404
    assert writes == []


def test_verify_requires_a_login(monkeypatch):
    writes = _patch_verify(monkeypatch, "mismatch")
    assert client.post("/documents/PO1/verify-totals").status_code == 401
    assert writes == []


def test_verify_is_refused_for_a_read_only_role(monkeypatch):
    writes = _patch_verify(monkeypatch, "mismatch")
    assert client.post("/documents/PO1/verify-totals", headers=_auth(role="viewer")).status_code == 403
    assert writes == []
