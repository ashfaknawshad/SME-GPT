"""Tests for the PO → DN → Invoice reconciliation state machine.

The rule function `reconcile_po_status` is pure, so these cover the full
delivery-driven fulfilment logic without any DB. `build_order_view` is exercised
with a monkeypatched record loader.
"""
import order_reconciliation as orec
from order_reconciliation import reconcile_po_status


def _po(status="pending"):
    return {"document_id": "PO1", "document_type": "po", "po_status": status, "order_id": "ORD-1"}


def _dn(status, doc_id="DN1"):
    return {"document_id": doc_id, "document_type": "dn", "dn_status": status, "order_id": "ORD-1"}


# ── reconcile_po_status (pure rule) ────────────────────────────────────────────

def test_no_delivery_notes_is_noop():
    assert reconcile_po_status(_po("pending"), []) is None


def test_all_delivered_marks_fulfilled():
    dns = [_dn("delivered", "DN1"), _dn("delivered", "DN2")]
    assert reconcile_po_status(_po("approved"), dns) == "fulfilled"


def test_some_delivered_marks_partial():
    dns = [_dn("delivered", "DN1"), _dn("pending", "DN2")]
    assert reconcile_po_status(_po("approved"), dns) == "partially_delivered"


def test_partial_dn_marks_partial():
    assert reconcile_po_status(_po("pending"), [_dn("partially_delivered")]) == "partially_delivered"


def test_dns_present_but_none_delivered_is_noop():
    # pending / delayed / failed / returned are not deliveries → leave PO as-is.
    dns = [_dn("pending", "DN1"), _dn("delayed", "DN2"), _dn("returned", "DN3")]
    assert reconcile_po_status(_po("approved"), dns) is None


def test_already_fulfilled_is_noop():
    assert reconcile_po_status(_po("fulfilled"), [_dn("delivered")]) is None


def test_terminal_states_never_overwritten():
    # A cancelled/rejected PO is a manual decision the cascade must respect,
    # even if a delivery note later shows delivered.
    assert reconcile_po_status(_po("cancelled"), [_dn("delivered")]) is None
    assert reconcile_po_status(_po("rejected"), [_dn("delivered")]) is None


def test_payment_does_not_affect_fulfilment():
    # Delivered-only rule: an unpaid but fully-delivered order is fulfilled.
    dns = [_dn("delivered")]
    assert reconcile_po_status(_po("approved"), dns) == "fulfilled"


def test_dn_delivered_via_received_status_field():
    # The Edit form sets received_status (not dn_status) on a DN, and PUT does
    # not re-derive dn_status — so reconciliation must honour received_status.
    dn = {"document_id": "DN1", "document_type": "dn",
          "dn_status": "pending", "received_status": "delivered", "order_id": "ORD-1"}
    assert reconcile_po_status(_po("approved"), [dn]) == "fulfilled"


def test_dn_status_takes_precedence_over_received_status():
    # Pipeline-derived dn_status wins when present.
    dn = {"document_id": "DN1", "document_type": "dn",
          "dn_status": "partially_delivered", "received_status": "delivered"}
    assert reconcile_po_status(_po("pending"), [dn]) == "partially_delivered"


# ── build_order_view (with a stubbed loader) ──────────────────────────────────

def test_build_order_view_composes_and_reconciles(monkeypatch):
    records = [
        {"document_id": "PO1", "document_type": "po", "po_status": "approved",
         "order_id": "ORD-1", "final_total_amount": "1000", "currency": "LKR"},
        {"document_id": "DN1", "document_type": "dn", "dn_status": "delivered",
         "order_id": "ORD-1"},
        {"document_id": "INV1", "document_type": "invoice", "invoice_status": "paid",
         "paid_status": "paid", "order_id": "ORD-1", "final_total_amount": "1000"},
        {"document_id": "PO9", "document_type": "po", "order_id": "OTHER"},  # different order
    ]
    monkeypatch.setattr(orec, "load_all_records", lambda user_id=None: records, raising=False)
    # load_all_records is imported inside _order_records; patch the source module.
    import dataset_manager
    monkeypatch.setattr(dataset_manager, "load_all_records", lambda user_id=None: records)

    view = orec.build_order_view("ORD-1", "tenant-1")
    assert view is not None
    assert view["counts"] == {"po": 1, "dn": 1, "invoice": 1}
    assert view["po_status"] == "fulfilled"       # reconciled from the delivered DN
    assert view["delivery_stage"] == "full"
    assert view["invoiced"] is True
    assert view["paid"] is True
    # PO first, then DN, then invoice; the OTHER-order PO is excluded.
    assert [d["document_id"] for d in view["documents"]] == ["PO1", "DN1", "INV1"]


def test_build_order_view_empty_returns_none(monkeypatch):
    import dataset_manager
    monkeypatch.setattr(dataset_manager, "load_all_records", lambda user_id=None: [])
    assert orec.build_order_view("NOPE", "tenant-1") is None
