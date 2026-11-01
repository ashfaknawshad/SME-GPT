"""PO -> DN -> Invoice linking, quantity-based fulfilment and recompute-with-
downgrade. Rule functions are pure; DB-backed wrappers use a stubbed loader."""
import order_reconciliation as orec
from order_reconciliation import reconcile_po_status


def _record_calls(monkeypatch):
    calls = []

    def fake(key, user_id, evidence_removed=False, label=""):
        calls.append((key, evidence_removed))
        return {}

    monkeypatch.setattr(orec, "_reconcile_key", fake)
    return calls


# ── Linking: order_key / link_key ─────────────────────────────────────────────

def test_order_key_ignores_separators_case_and_po_label():
    same = ["PO-2026-004", "po 2026/004", "P.O. No: 2026-004", "2026-004", "PO2026004"]
    assert {orec.order_key(v) for v in same} == {"2026004"}


def test_order_key_nullish_is_empty_and_keeps_bare_label():
    for v in (None, "", "NULL", "none", " - "):
        assert orec.order_key(v) == ""
    assert orec.order_key("PO") == "PO"


def test_link_key_po_uses_own_number_dn_uses_reference():
    po = {"document_type": "po", "order_id": "PO-2026-004"}
    dn = {"document_type": "dn", "order_id": "DN-0042", "po_reference": "PO 2026 004"}
    assert orec.link_key(po) == orec.link_key(dn) == "2026004"


def test_link_key_falls_back_to_order_id_for_legacy_records():
    # Before po_reference existed, a DN's order_id held the PO number.
    dn = {"document_type": "dn", "order_id": "PO-EXPO-1001", "po_reference": "NULL"}
    assert orec.link_key(dn) == orec.link_key({"document_type": "po", "order_id": "PO-EXPO-1001"})


def test_link_key_reads_reference_from_structured_json_before_migration():
    dn = {"document_type": "dn", "order_id": "DN-1",
          "structured_json": '{"po_reference": "PO-77"}'}
    assert orec.link_key(dn) == "77"


# ── Quantity-based fulfilment ─────────────────────────────────────────────────

def _po(status="approved", items=None, approved_by=None):
    return {"document_id": "PO1", "document_type": "po", "po_status": status,
            "order_id": "PO-1", "approved_by": approved_by,
            "items": items if items is not None else [
                {"description": "A4 Paper (500 sheets)", "quantity": "10"},
                {"description": "Whiteboard Markers", "quantity": "5"},
            ]}


def _dn(items, status="delivered", doc_id="DN1"):
    return {"document_id": doc_id, "document_type": "dn", "dn_status": status,
            "po_reference": "PO-1", "items": items}


def _full_delivery():
    return [{"description": "A4 Paper (500 sheets)", "quantity": "10"},
            {"description": "Whiteboard Markers", "quantity": "5"}]


def test_full_quantities_fulfil():
    assert reconcile_po_status(_po(), [_dn(_full_delivery())]) == "fulfilled"


def test_short_delivery_is_partial_even_when_dn_says_delivered():
    dn = _dn([{"description": "A4 Paper (500 sheets)", "quantity": "5"},
              {"description": "Whiteboard Markers", "quantity": "5"}])
    assert reconcile_po_status(_po(), [dn]) == "partially_delivered"
    delivery = orec.compute_delivery(_po(), [dn])
    assert delivery["mode"] == "quantity"
    delivered = {line["description"]: line["delivered"] for line in delivery["lines"]}
    assert delivered["A4 Paper (500 sheets)"] == 5


def test_quantities_add_up_across_delivery_notes():
    dn1 = _dn([{"description": "A4 Paper (500 sheets)", "quantity": "6"}], doc_id="DN1")
    dn2 = _dn([{"description": "A4 Paper (500 sheets)", "quantity": "4"},
               {"description": "Whiteboard Markers", "quantity": "5"}], doc_id="DN2")
    assert reconcile_po_status(_po(), [dn1, dn2]) == "fulfilled"


def test_ocr_noise_in_descriptions_still_matches():
    dn = _dn([{"description": "A4 paper 500 sheet", "quantity": "10"},
              {"description": "WHITEBOARD MARKERS.", "quantity": "5"}])
    assert reconcile_po_status(_po(), [dn]) == "fulfilled"


def test_sinhala_descriptions_match():
    po = _po(items=[{"description": "සහල් (1kg)", "quantity": "2"}])
    dn = _dn([{"description": "සහල් 1kg", "quantity": "2"}])
    assert reconcile_po_status(po, [dn]) == "fulfilled"


def test_unrecognised_item_does_not_fulfil_and_is_reported():
    dn = _dn([{"description": "Office Chair", "quantity": "10"}])
    assert reconcile_po_status(_po(), [dn]) == "partially_delivered"
    assert orec.compute_delivery(_po(), [dn])["unmatched"] == [
        {"description": "Office Chair", "quantity": 10.0}]


def test_partial_dn_quantities_do_not_count_towards_fulfilment():
    dn = _dn(_full_delivery(), status="partially_delivered")
    assert reconcile_po_status(_po(), [dn]) == "partially_delivered"


def test_dn_without_items_falls_back_to_status_rule():
    dn = _dn([], status="delivered")
    assert orec.compute_delivery(_po(), [dn])["mode"] == "status"
    assert reconcile_po_status(_po(), [dn]) == "fulfilled"


def test_po_without_quantities_falls_back_to_status_rule():
    po = _po(items=[{"description": "Consulting", "quantity": ""}])
    assert reconcile_po_status(po, [_dn(_full_delivery())]) == "fulfilled"


# ── Recompute with downgrade ──────────────────────────────────────────────────

def test_undelivered_dn_downgrades_fulfilled_po():
    dn = _dn([], status="returned")
    assert reconcile_po_status(_po("fulfilled", approved_by="Kasun"), [dn]) == "approved"
    assert reconcile_po_status(_po("partially_delivered"), [dn]) == "pending"


def test_removed_evidence_downgrades_but_only_when_flagged():
    po = _po("fulfilled")
    assert reconcile_po_status(po, []) is None   # never had DNs: manual status kept
    assert reconcile_po_status(po, [], evidence_removed=True) == "pending"


def test_downgrade_never_touches_terminal_or_manual_states():
    assert reconcile_po_status(_po("cancelled"), [], evidence_removed=True) is None
    assert reconcile_po_status(_po("approved"), [], evidence_removed=True) is None


# ── reconcile_after_change: which orders get recomputed ───────────────────────

def test_moving_a_dn_reconciles_old_order_with_evidence_removed(monkeypatch):
    calls = _record_calls(monkeypatch)
    before = {"document_type": "dn", "order_id": "DN-1", "po_reference": "PO-1"}
    after = {"document_type": "dn", "order_id": "DN-1", "po_reference": "PO-2"}
    orec.reconcile_after_change("t1", before, after)
    assert calls == [("1", True), ("2", False)]


def test_deleting_a_dn_reconciles_its_order(monkeypatch):
    calls = _record_calls(monkeypatch)
    orec.reconcile_after_change("t1", {"document_type": "dn", "po_reference": "PO-1"}, None)
    assert calls == [("1", True)]


def test_unchanged_link_reconciles_once(monkeypatch):
    calls = _record_calls(monkeypatch)
    doc = {"document_type": "dn", "po_reference": "PO-1", "dn_status": "delivered"}
    orec.reconcile_after_change("t1", doc, dict(doc))
    assert calls == [("1", False)]


def test_reconcile_failure_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(orec, "_reconcile_key", boom)
    assert orec.reconcile_after_change("t1", None, {"document_type": "po", "order_id": "PO-1"}) == []


def test_reconcile_key_persists_change(monkeypatch):
    import dataset_manager

    records = [
        {"user_id": "t1", "document_id": "PO1", "document_type": "po", "po_status": "approved",
         "order_id": "PO-1", "items": []},
        {"user_id": "t1", "document_id": "DN1", "document_type": "dn", "dn_status": "delivered",
         "po_reference": "PO-1", "items": []},
    ]
    writes = []
    monkeypatch.setattr(dataset_manager, "load_all_records", lambda user_id=None: records)
    monkeypatch.setattr(dataset_manager, "update_record_for_user",
                        lambda user_id, doc_id, updates: writes.append((doc_id, updates)))
    summary = orec.reconcile_order("po 1", "t1")
    assert writes == [("PO1", {"po_status": "fulfilled"})]
    assert summary["changes"] == [{"document_id": "PO1", "from": "approved", "to": "fulfilled"}]


# ── Order view ────────────────────────────────────────────────────────────────

def test_order_view_links_by_reference_and_cleans_null_status(monkeypatch):
    import dataset_manager

    records = [
        {"document_id": "PO7", "document_type": "po", "po_status": "NULL", "order_id": "PO-EXPO-1002",
         "items": [{"description": "Tent", "quantity": "2"}]},
        {"document_id": "DN9", "document_type": "dn", "dn_status": "pending",
         "order_id": "DN-9", "po_reference": "po expo 1002",
         "items": [{"description": "Tent", "quantity": "2"}]},
    ]
    monkeypatch.setattr(dataset_manager, "load_all_records", lambda user_id=None: records)
    view = orec.order_view_for_document(records[1], "t1")
    assert [d["document_id"] for d in view["documents"]] == ["PO7", "DN9"]
    assert view["documents"][0]["po_status"] is None   # "NULL" never reaches the UI
    assert view["po_status"] == "pending"
    assert view["order_id"] == "PO-EXPO-1002"


# ── Extraction keeps po_reference only where it means something ───────────────

def test_extraction_po_reference_rules():
    from ocr_to_json_extractor import normalize_root_fields

    def ref(doc_type, order_id, po_ref):
        return normalize_root_fields(
            {"document_type": doc_type, "order_id": order_id, "po_reference": po_ref}, "text"
        )["po_reference"]

    assert ref("dn", "DN-0042", "PO-2026-004") == "PO-2026-004"
    assert ref("invoice", "INV-9", "PO-2026-004") == "PO-2026-004"
    assert ref("po", "PO-2026-004", "PO-2026-004") == ""   # a PO has no parent PO
    assert ref("receipt", "R-1", "PO-1") == ""
    assert ref("dn", "DN-0042", "dn-0042") == ""           # copied its own number
