"""Component 1 — Semantic OCR Post-Correction (box-level).

Operates per OCR box, not whole-page text blobs (see docs/components/component-1.md).
Each box from an OCRService (ocr_service.py) is corrected independently via
DeepSeek, then passed through the numeric safeguard `safe_correct()` before being
accepted. The safeguard is deterministic and non-negotiable: DeepSeek may fix
spelling, but it can never be trusted to alter a number.

This module does not replace the live whole-text correction path in
llm_correction.py (still used by document_pipeline.py against the v1 Surya
output) — it reuses its token-masking/DeepSeek-call helpers and adds the new
box-granular contract for C1 going forward.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from llm_correction import (
    correct_via_llm,
    count_sinhala_chars,
    has_correctable_text,
    looks_like_bad_rewrite,
    preserve_sensitive_tokens,
    restore_sensitive_tokens,
    strip_llm_boilerplate,
)

_DIGIT_RE = re.compile(r"\d")


def extract_digits(text: str) -> str:
    """All digit characters in order, ignoring everything else (commas, spaces, letters)."""
    if not isinstance(text, str):
        return ""
    return "".join(_DIGIT_RE.findall(text))


def _decimal_skeleton(text: str) -> str:
    """Digits collapsed to 'd', decimal points kept, everything else dropped.
    Catches a corruption that `extract_digits` alone misses: "500.00" -> "5000.0"
    has the same digit sequence (50000) but a different decimal position.

    Only a '.' sitting between two digits counts as a decimal point — this
    avoids false rejections from unrelated periods (e.g. the abbreviation
    "Rs." next to a number)."""
    if not isinstance(text, str):
        return ""
    skeleton = []
    for i, ch in enumerate(text):
        if ch.isdigit():
            skeleton.append("d")
        elif ch == "." and 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit():
            skeleton.append(".")
    return "".join(skeleton)


def safe_correct(raw: str, corrected: str) -> tuple[str, str]:
    """Reject the correction if digit count, digit sequence, or decimal structure
    differ between raw and corrected. Returns (final_text, source)."""
    if extract_digits(raw) != extract_digits(corrected):
        return raw, "raw_ocr"
    if _decimal_skeleton(raw) != _decimal_skeleton(corrected):
        return raw, "raw_ocr"
    return corrected, "ai_corrected"


_BOX_PROMPT = """You are correcting a single OCR text box from a financial document.

STRICT RULES:
- Correct spelling mistakes only
- Preserve all digits, prices, dates, and IDs exactly as given
- Preserve Sinhala script; never translate or transliterate it
- This is one isolated box, not a full page — do not add context, notes, or extra words
- Return ONLY the corrected text, nothing else

Text box:
{masked_text}
""".strip()


def correct_box(box: dict) -> dict:
    """Correct one canonical OCR box. Always returns the box's fields plus
    `source` ("ai_corrected" | "raw_ocr") and `locked_digits` (bool)."""
    raw_text = box.get("text", "") or ""
    result = dict(box)

    if not raw_text.strip():
        result["text"] = raw_text
        result["source"] = "raw_ocr"
        result["locked_digits"] = False
        return result

    has_digits = bool(extract_digits(raw_text))
    masked, placeholders = preserve_sensitive_tokens(raw_text)

    if not has_correctable_text(masked):
        result["text"] = raw_text
        result["source"] = "raw_ocr"
        result["locked_digits"] = has_digits
        return result

    try:
        raw_reply = correct_via_llm(_BOX_PROMPT.format(masked_text=masked))
    except Exception:
        # DeepSeek unavailable/timed out -> keep raw OCR text (component-1.md failure table).
        result["text"] = raw_text
        result["source"] = "raw_ocr"
        result["locked_digits"] = has_digits
        return result

    corrected = strip_llm_boilerplate(raw_reply)
    corrected = restore_sensitive_tokens(corrected, placeholders)

    if not corrected.strip() or looks_like_bad_rewrite(raw_text, corrected):
        corrected = raw_text

    original_si = count_sinhala_chars(raw_text)
    if original_si >= 8 and count_sinhala_chars(corrected) < max(2, int(original_si * 0.3)):
        corrected = raw_text

    final_text, source = safe_correct(raw_text, corrected)

    result["text"] = final_text
    result["source"] = source
    result["locked_digits"] = has_digits
    return result


def correct_pages(pages: list[list[dict]]) -> list[dict]:
    """pages: one canonical box list per page (e.g. from OCRService.run()).
    Returns the final_safe_boxes.json structure: [{"page": n, "boxes": [...]}, ...]."""
    return [
        {"page": page_number, "boxes": [correct_box(box) for box in boxes]}
        for page_number, boxes in enumerate(pages, start=1)
    ]


def write_final_safe_boxes(corrected_pages: list[dict], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(corrected_pages, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def build_quality_report(corrected_pages: list[dict]) -> dict:
    """CER/NAR scaffolding (docs/TESTING.md §3). NAR is guaranteed 1.0 by
    construction (safe_correct() never lets a digit-altering correction through);
    CER needs a ground-truth transcript per sample doc, which doesn't exist yet,
    so it's reported as null until one is added to backend/tests/fixtures/."""
    total_boxes = 0
    ai_corrected = 0
    raw_kept = 0
    numeric_boxes = 0

    for page in corrected_pages:
        for box in page.get("boxes", []):
            total_boxes += 1
            if box.get("source") == "ai_corrected":
                ai_corrected += 1
            else:
                raw_kept += 1
            if box.get("locked_digits"):
                numeric_boxes += 1

    return {
        "total_boxes": total_boxes,
        "ai_corrected_boxes": ai_corrected,
        "raw_ocr_boxes": raw_kept,
        "numeric_boxes": numeric_boxes,
        "numeric_accuracy_rate": 1.0 if total_boxes else None,
        "character_error_rate": None,
    }
