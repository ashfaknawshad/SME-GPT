"""Line items vs document total — regression tests.

Covers the bugs found with a handwritten receipt (Bristol board 80, glue 110,
paper cutter 50, total 240) where each line shows a single amount and no
quantity or unit price:

1. Blank qty/unit price used to become 0, so the items summed to 0 and the
   real total was overwritten with 0.
2. A payable amount filled in from the items was then compared with the items,
   so a genuine mismatch was always reported as "matched".
3. Tax was ignored when checking items against the total.
"""
import pytest

import correction_engine as ce
from arithmetic_validator import validate_arithmetic
from ocr_to_json_extractor import normalize_items as extractor_normalize_items


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    # Item descriptions go through an LLM text clean-up; not under test here.
    monkeypatch.setattr(
        ce, "hybrid_correct_text",
        lambda text: {"final_text": text, "confidence_score": 0.9},
    )


def _row(description, quantity, unit_price, line_total):
    return {"description": description, "quantity": quantity,
            "unit_price": unit_price, "line_total": line_total}


def _process(items, final="", raw="", payable="", tax=""):
    """Same steps as the upload pipeline: extractor normalisation →
    correction engine → arithmetic validation."""
    parsed = {
        "items": extractor_normalize_items(items),
        "final_total_amount": final,
        "raw_total_amount": raw,
        "payable_amount": payable,
        "tax_amount": tax,
    }
    data = ce.correct_extracted_fields(parsed)
    validation = validate_arithmetic(data)
    return data, validation


NAMES = ["Bristol board", "Glue", "Paper cutter"]
AMOUNTS = ["80", "110", "50"]


@pytest.mark.parametrize("make_row", [
    pytest.param(lambda n, a: _row(n, "1", a, a), id="qty-price-total"),
    pytest.param(lambda n, a: _row(n, "", "", a), id="line-total-only"),
    pytest.param(lambda n, a: _row(n, "", a, ""), id="unit-price-only"),
    pytest.param(lambda n, a: _row(n, "1", "", a), id="qty-and-line-total"),
])
def test_handwritten_receipt_totals_match(make_row):
    items = [make_row(n, a) for n, a in zip(NAMES, AMOUNTS)]
    data, validation = _process(items, final="240")

    assert validation["computed_items_total"] == 240.0
    assert validation["status"] == "matched"
    assert float(data["final_total_amount"]) == 240.0
    assert [i["line_total"] for i in data["items"]] == [80.0, 110.0, 50.0]
    assert [i["quantity"] for i in data["items"]] == [1.0, 1.0, 1.0]


def test_missing_quantity_derived_from_price_and_line_total():
    data, validation = _process([_row("Tiles", "", "780", "31200")], final="31200")
    assert data["items"][0]["quantity"] == 40.0
    assert validation["status"] == "matched"


def test_misread_item_is_flagged_not_hidden_by_derived_payable():
    # OCR reads 110 as 1100: items sum to 1230 but the printed total is 240.
    items = [_row(n, "1", a, a) for n, a in zip(NAMES, ["80", "1100", "50"])]
    data, validation = _process(items, final="240")

    assert validation["status"] == "mismatch"
    assert "payable_amount" in data["derived_totals"]
    assert [d["field"] for d in validation["discrepancies"]] == ["final_total_amount"]
    assert validation["recommended"]["final_total_amount"] == 1230.0


def test_mismatch_recommends_items_sum():
    # Printed total misread (240/= read as 242): final total becomes the items sum.
    data, validation = _process([_row(n, "1", a, a) for n, a in zip(NAMES, AMOUNTS)], final="242")
    assert validation["status"] == "mismatch"
    assert validation["recommended"]["final_total_amount"] == 240.0
    assert validation["recommended"]["payable_amount"] == 240.0


def test_mismatch_recommendation_includes_tax():
    _, validation = _process([_row("Paint", "1", "1000", "1000")], final="1300", tax="150")
    assert validation["status"] == "mismatch"
    assert validation["recommended"]["final_total_amount"] == 1150.0


def test_items_plus_tax_matches_final_total():
    data, validation = _process([_row("Paint", "1", "1000", "1000")], final="1150", tax="150")
    assert validation["checks"]["final_total_matches_items"] is True
    assert validation["status"] == "matched"
    assert validation["discrepancies"] == []


def test_wrong_total_with_tax_still_mismatch():
    _, validation = _process([_row("Paint", "1", "1000", "1000")], final="1300", tax="150")
    assert validation["status"] == "mismatch"


def test_totals_derived_entirely_from_items_are_matched():
    # Nothing printed to compare against: totals come from the items.
    data, validation = _process([_row(n, "1", a, a) for n, a in zip(NAMES, AMOUNTS)])
    assert float(data["final_total_amount"]) == 240.0
    assert validation["status"] == "matched"


def test_no_items_is_low_confidence():
    _, validation = _process([], final="240")
    assert validation["status"] == "low_confidence"


def test_explicit_qty_times_price_still_preferred():
    # qty x unit price wins over a contradictory line total (existing rule).
    _, validation = _process([_row("Keyboard", "25", "89", "800")], final="2225")
    assert validation["computed_items_total"] == 2225.0
    assert validation["status"] == "matched"
