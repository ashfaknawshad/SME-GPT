"""A PO's status follows delivery evidence in both directions: only Delivery
Notes marked delivered count, and removing that evidence takes the status back.
Pure rule tests; the DB-backed linking is covered in test_document_linking."""
from correction_engine import normalize_items
from arithmetic_validator import compute_items_total
from document_links import prepare
from order_matching import compute_order
from order_reconciliation import _lost_delivery_updates
from references import derive_references


def _po(status="approved", approved_by=None):
    return prepare({
        "document_id": "PO1", "document_type": "po", "order_id": "PO-1", "po_status": status,
        "approved_by": approved_by, "items": [
            {"description": "Portland Cement 50kg", "quantity": 100, "unit_price": 2000},
            {"description": "Steel Rod 12mm", "quantity": 40, "unit_price": 500},
        ],
    })


def _dn(status, cement=100, steel=40, doc_id="DN1"):
    items = [{"description": "Portland Cement 50kg", "quantity": cement},
             {"description": "Steel Rod 12mm", "quantity": steel}]
    return prepare({"document_id": doc_id, "document_type": "dn", "dn_status": status,
                    "po_ref": "PO-1", "items": items})


def _order(po, *dns):
    return compute_order([po, *dns], [
        {"from": d["document_id"], "to": "PO1", "link_type": "po_dn", "status": "auto"} for d in dns
    ])


def _po_update(view):
    return next((u["to"] for u in view["updates"] if u["field"] == "po_status"), None)


def test_pending_dn_does_not_fulfil_a_po():
    view = _order(_po(), _dn("pending"))
    assert view["delivery_stage"] == "none"
    assert _po_update(view) is None
    assert all(line["delivered"] == 0 for line in view["lines"])


def test_dn_with_no_status_is_not_delivery_evidence():
    assert _order(_po(), _dn(None))["delivery_stage"] == "none"


def test_short_delivery_stays_partial_even_when_marked_delivered():
    assert _po_update(_order(_po(), _dn("delivered", cement=60))) == "partially_delivered"


def test_quantities_add_up_across_delivered_notes_only():
    first = _dn("delivered", cement=60, steel=40, doc_id="DN1")
    second = _dn("delivered", cement=40, steel=0, doc_id="DN2")
    assert _po_update(_order(_po(), first, second)) == "fulfilled"
    # The same second note still in transit doesn't count yet.
    in_transit = _dn("pending", cement=40, steel=0, doc_id="DN2")
    assert _po_update(_order(_po(), first, in_transit)) == "partially_delivered"


def test_partially_delivered_note_quantities_do_not_fulfil():
    assert _po_update(_order(_po(), _dn("partially_delivered"))) == "partially_delivered"


def test_delivered_items_matching_nothing_on_the_po_do_not_fulfil():
    other = prepare({"document_id": "DN1", "document_type": "dn", "dn_status": "delivered", "po_ref": "PO-1",
                     "items": [{"description": "Office Chair", "quantity": 100}]})
    assert _po_update(_order(_po(), other)) == "partially_delivered"


def test_undelivered_note_takes_a_fulfilled_po_back():
    assert _po_update(_order(_po("fulfilled", approved_by="Kasun"), _dn("returned"))) == "approved"
    assert _po_update(_order(_po("partially_delivered"), _dn("pending"))) == "pending"


def test_manual_terminal_status_is_never_changed():
    assert _po_update(_order(_po("cancelled"), _dn("delivered"))) is None
    assert _po_update(_order(_po("rejected"), _dn("pending"))) is None


def _links(*pairs):
    return [{"from": f, "to": t, "link_type": "po_dn", "status": "auto"} for f, t in pairs]


def test_po_losing_its_last_delivery_note_reverts():
    prepared = {"PO1": _po("fulfilled", approved_by="Kasun")}
    updates = _lost_delivery_updates(prepared, _links(("DN1", "PO1")), [])
    assert updates == [{"document_id": "PO1", "field": "po_status", "from": "fulfilled", "to": "approved"}]


def test_link_to_a_deleted_note_does_not_count_as_evidence():
    prepared = {"PO1": _po("fulfilled")}
    updates = _lost_delivery_updates(prepared, _links(("DN1", "PO1")), _links(("DN1", "PO1")))
    assert [u["to"] for u in updates] == ["pending"]


def test_po_that_never_had_a_note_keeps_its_status():
    assert _lost_delivery_updates({"PO1": _po("fulfilled")}, [], []) == []


def test_po_still_linked_to_a_note_is_left_to_compute_order():
    prepared = {"PO1": _po("fulfilled"), "DN1": _dn("delivered")}
    links = _links(("DN1", "PO1"))
    assert _lost_delivery_updates(prepared, links, links) == []


def test_saved_po_reference_still_links():
    # Values saved in the poReference column before po_ref existed.
    dn = {"document_id": "DN5", "document_type": "dn", "order_id": "DN-AIESEC-582741",
          "po_reference": "PO-AIESEC-582741"}
    assert derive_references(dn)["po_ref"] == "PO-AIESEC-582741"


def test_line_total_read_as_unit_price_is_not_multiplied_again():
    # A receipt printing 2 x 220 = 440 and 0.5 x 450 = 225, with the total
    # column extracted as the unit price.
    items = normalize_items([
        {"description": "Rice", "quantity": 2, "unit_price": 440, "line_total": 440},
        {"description": "Tomato", "quantity": 0.5, "unit_price": 225, "line_total": 225},
        {"description": "Milk", "quantity": 1, "unit_price": 1450, "line_total": 1450},
    ])
    assert [i["unit_price"] for i in items] == [220, 450, 1450]
    assert compute_items_total(items)["computed_total"] == 2115
