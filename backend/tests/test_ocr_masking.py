"""Numbers/ids are masked before OCR correction and come back unchanged.

Regression cover for two masking bugs:
  * token-by-token str.replace let a later token ("2") land inside an earlier
    placeholder ("__TOKEN_2__"), so a date came back as "__TOKEN_2__";
  * all-caps WORDS ("INVIOCE") were masked, so the LLM could never fix them.
And for the digit lock on the LLM pass (it had only masking to rely on).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import llm_correction
from llm_correction import (
    dictionary_correct_text,
    llm_refine_text,
    preserve_sensitive_tokens,
    restore_sensitive_tokens,
)

RECEIPT = (
    "INVIOCE No: INV-0045  Date: 12/03/2025\n"
    "Thakkaali 2 x 250.00 = 500.00\n"
    "Toatl Amout: 1,250.50\n"
    "Call 0112-456789"
)


def test_round_trip_is_exact():
    masked, placeholders = preserve_sensitive_tokens(RECEIPT)
    assert restore_sensitive_tokens(masked, placeholders) == RECEIPT


def test_small_number_after_many_tokens_does_not_corrupt_a_placeholder():
    masked, placeholders = preserve_sensitive_tokens(RECEIPT)
    # Every value is hidden, each behind exactly one well-formed placeholder.
    assert "12/03/2025" not in masked and "250.00" not in masked
    assert "TOKEN___" not in masked
    assert sorted(placeholders.values()) == sorted(
        ["INV-0045", "12/03/2025", "2", "250.00", "500.00", "1,250.50", "0112-456789"]
    )


def test_values_are_masked_but_capitalised_words_are_not():
    masked, placeholders = preserve_sensitive_tokens("INVIOCE TOTAL PO-10045 SKU77 ABC1234")
    assert masked.startswith("INVIOCE TOTAL ")
    assert set(placeholders.values()) == {"PO-10045", "SKU77", "ABC1234"}


def test_id_prefix_words_without_digits_stay_visible():
    # Used to be masked by the INV/PO/REC prefix rules.
    masked, placeholders = preserve_sensitive_tokens("RECEIPT POWER INVOICE")
    assert masked == "RECEIPT POWER INVOICE" and placeholders == {}


def test_dictionary_pass_keeps_every_value():
    out = dictionary_correct_text(RECEIPT.replace("\n", " "))
    for value in ["INV-0045", "12/03/2025", "250.00", "500.00", "1,250.50", "0112-456789"]:
        assert value in out
    assert "__TOKEN" not in out


def _fake_llm(monkeypatch, reply_for):
    monkeypatch.setattr(llm_correction, "correct_via_llm", lambda prompt: reply_for(prompt))


def _masked_part(prompt: str) -> str:
    return prompt.split("OCR text to correct:\n", 1)[1]


def test_llm_fixes_words_and_values_come_back(monkeypatch):
    _fake_llm(monkeypatch, lambda p: _masked_part(p).replace("INVIOCE", "INVOICE"))
    out = llm_refine_text(RECEIPT)
    assert "INVOICE No: INV-0045" in out
    assert "Date: 12/03/2025" in out
    assert "1,250.50" in out and "__TOKEN" not in out


def test_llm_never_sees_the_values(monkeypatch):
    seen = {}

    def reply(prompt):
        seen["text"] = _masked_part(prompt)
        return seen["text"]

    _fake_llm(monkeypatch, reply)
    llm_refine_text(RECEIPT)
    for value in ["12/03/2025", "250.00", "1,250.50", "0112-456789"]:
        assert value not in seen["text"]


def test_llm_that_drops_a_placeholder_is_rejected(monkeypatch):
    def reply(prompt):
        masked = _masked_part(prompt).replace("INVIOCE", "INVOICE")
        return masked.replace("__TOKEN_2__", "", 1)  # loses the date

    _fake_llm(monkeypatch, reply)
    out = llm_refine_text(RECEIPT)
    assert "12/03/2025" in out          # fell back to the uncorrected text
    assert "INVIOCE" in out


def test_llm_that_types_a_digit_is_rejected(monkeypatch):
    _fake_llm(monkeypatch, lambda p: _masked_part(p).replace("Amout:", "Amount: 9"))
    out = llm_refine_text(RECEIPT)
    assert "Amout" in out and "Amount: 9" not in out


def test_llm_that_invents_a_placeholder_is_rejected(monkeypatch):
    _fake_llm(monkeypatch, lambda p: _masked_part(p) + " __TOKEN_99__")
    out = llm_refine_text(RECEIPT)
    assert "__TOKEN" not in out
