import os
import time
import shutil
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import cv2
import numpy as np
from deskew import determine_skew
from pdf2image import convert_from_path

from colab_ocr_client import send_images_to_colab_ocr
from ocr_selector import select_best_ocr_version
from llm_correction import llm_refine_text, clean_ocr_text, correct_boxes_for_page
from spatial_serializer import serialize_safe_boxes
from spatial_serialization import build_spatial_chunks as _build_rich_spatial_chunks
from ocr_to_json_extractor import extract_structured_json_from_text
from arithmetic_validator import validate_arithmetic
from correction_engine import correct_extracted_fields

COLAB_OCR_URL = os.getenv("COLAB_OCR_URL", "").strip()
POPPLER_PATH = os.getenv("POPPLER_PATH", "")


class OCREngineUnavailableError(Exception):
    """Raised when Colab OCR can't be reached. Colab is the only supported
    OCR engine (per user decision -- no local fallback): the local Surya
    path required a model install we don't ship, and silently falling back
    to it just turned a clear "Colab is down" signal into a content-free
    500 from the global exception handler. The message here is fixed and
    safe to show to users -- it never wraps the raw connection error."""

    def __init__(self):
        super().__init__(
            "OCR engine unavailable. The Colab OCR server isn't reachable "
            "-- make sure the notebook is running and COLAB_OCR_URL is set "
            "to its current ngrok URL, then try again."
        )

# Image preprocessing constants
_TARGET_WIDTH_PX      = 1600
_DENOISE_H            = 12
_DENOISE_TEMPLATE_WIN = 7
_DENOISE_SEARCH_WIN   = 21
_ADAPTIVE_BLOCK_SIZE  = 31
_ADAPTIVE_C           = 9
_BILATERAL_D          = 9
_BILATERAL_SIGMA      = 60

TEMP_BASE = Path("temp_processing")
RAW_DIR = TEMP_BASE / "raw"
PAGES_DIR = TEMP_BASE / "pages"
ORIG_DIR = PAGES_DIR / "orig"
P_DIR = PAGES_DIR / "P"
M_DIR = PAGES_DIR / "M"

def ensure_dirs():
    for d in [RAW_DIR, ORIG_DIR, P_DIR, M_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _safe_remove_file(path: Path, retries: int = 5, delay: float = 0.3):
    for i in range(retries):
        try:
            if path.exists():
                path.unlink()
            return
        except PermissionError:
            if i == retries - 1:
                raise
            time.sleep(delay)


def _safe_remove_dir_contents(folder: Path):
    if not folder.exists():
        return

    for child in folder.iterdir():
        if child.is_file():
            _safe_remove_file(child)
        elif child.is_dir():
            shutil.rmtree(child, ignore_errors=True)


def save_uploaded_file(upload_path: str) -> Path:
    ensure_dirs()

    src = Path(upload_path)
    if not src.exists():
        raise FileNotFoundError(f"Uploaded file not found: {upload_path}")

    # Clean only old files if needed, but do not wipe everything on every upload
    dst = RAW_DIR / src.name
    shutil.copy(src, dst)
    return dst

def standardize_to_images(raw_file_path: Path) -> list[Path]:
    output_paths: list[Path] = []

    if raw_file_path.suffix.lower() == ".pdf":
        if POPPLER_PATH:
            images = convert_from_path(str(raw_file_path), dpi=300, poppler_path=POPPLER_PATH)
        else:
            images = convert_from_path(str(raw_file_path), dpi=300)

        if not images:
            raise ValueError("No pages found in uploaded PDF.")

        for i, image in enumerate(images, start=1):
            output_path = ORIG_DIR / f"page_{i:03d}.png"
            image.save(output_path, "PNG")
            output_paths.append(output_path)            
    else:
        output_path = ORIG_DIR / "page_001.png"
        shutil.copy(raw_file_path, output_path)
        output_paths.append(output_path)

    return output_paths

def _deskew_image(img: np.ndarray) -> np.ndarray:
    """Corrects skew (FR-03) for phone-photo documents. Angles under 0.3deg are
    treated as noise -- determine_skew() never returns exactly 0 even for a
    perfectly straight scan, and rotating a near-zero angle just re-samples
    the image (a quality loss) for no benefit."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    angle = determine_skew(gray)
    if angle is None or abs(angle) < 0.3:
        return img

    h, w = img.shape[:2]
    rot_mat = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        img, rot_mat, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def preprocess_images(orig_paths: list[Path]) -> list[dict]:
    processed_pages = []
    for orig_path in orig_paths:
        filename = orig_path.name
        p_path = P_DIR / filename
        m_path = M_DIR / filename

        img = cv2.imread(str(orig_path))
        if img is None:
            raise ValueError(f"Failed to read standardized image: {orig_path}")

        img = _deskew_image(img)

        h, w = img.shape[:2]

        if w < _TARGET_WIDTH_PX:
            scale = _TARGET_WIDTH_PX / w
            img = cv2.resize(
                img,
                (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_CUBIC
            )

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Printed-friendly version
        den = cv2.fastNlMeansDenoising(gray, None, _DENOISE_H, _DENOISE_TEMPLATE_WIN, _DENOISE_SEARCH_WIN)
        p_img = cv2.adaptiveThreshold(
            den,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            _ADAPTIVE_BLOCK_SIZE,
            _ADAPTIVE_C
        )
        cv2.imwrite(str(p_path), p_img)

        # Messy-document-friendly version
        m_img = cv2.bilateralFilter(gray, _BILATERAL_D, _BILATERAL_SIGMA, _BILATERAL_SIGMA)
        m_img = cv2.normalize(m_img, None, 0, 255, cv2.NORM_MINMAX)
        cv2.imwrite(str(m_path), m_img)

        processed_pages.append({
            "page_name": filename,
            "orig": str(orig_path),
            "P": str(p_path),
            "M": str(m_path),
        })

    return processed_pages


def _merge_page_texts(page_entries: list) -> str:
    merged_parts = []

    for idx, page_entry in enumerate(page_entries, start=1):
        if isinstance(page_entry, dict):
            page_text = str(page_entry.get("text", "") or "").strip()
        else:
            page_text = str(page_entry or "").strip()

        if not page_text:
            continue

        merged_parts.append(f"=== PAGE {idx} ===\n{page_text}")

    return "\n\n".join(merged_parts).strip()


def _normalize_multi_page_ocr_result(ocr_result: dict) -> dict:
    versions = ocr_result.get("versions", {}) or {}
    failures = ocr_result.get("failures", {}) or {}
    normalized_versions = {}

    for version_name, version_data in versions.items():
        if isinstance(version_data, dict):
            pages = version_data.get("pages", []) or []
            merged_text = version_data.get("text", "") or ""

            if pages and not merged_text.strip():
                merged_text = _merge_page_texts(pages)
            elif pages and merged_text.strip():
                merged_text = merged_text.strip()
            else:
                merged_text = str(merged_text or "").strip()

            normalized_versions[version_name] = {
                "text": merged_text,
                "pages": pages,
            }

        elif isinstance(version_data, list):
            merged_text = _merge_page_texts(version_data)
            normalized_versions[version_name] = {
                "text": merged_text,
                "pages": version_data,
            }
        else:
            normalized_versions[version_name] = {
                "text": str(version_data or "").strip(),
                "pages": [],
            }

    return {
        "versions": normalized_versions,
        "failures": failures,
    }
def build_preview_from_versions(version_paths: list[dict]) -> dict:
    final_colab_url = COLAB_OCR_URL or os.getenv("COLAB_OCR_URL", "").strip()

    if not final_colab_url:
        print("[PIPELINE] No COLAB_OCR_URL set — OCR unavailable", flush=True)
        raise OCREngineUnavailableError()

    try:
        print("[PIPELINE] Trying Colab OCR...", flush=True)
        ocr_result = send_images_to_colab_ocr(
            processed_pages=version_paths,
            colab_url=final_colab_url,
        )
        print("[PIPELINE] Colab OCR completed", flush=True)
    except Exception as e:
        print(f"[PIPELINE] Colab OCR failed: {e}", flush=True)
        raise OCREngineUnavailableError() from e

    normalized_ocr = _normalize_multi_page_ocr_result(ocr_result)
    versions = normalized_ocr.get("versions", {})
    failures = normalized_ocr.get("failures", {})

    if not versions:
        raise ValueError(f"No OCR versions returned from Colab OCR API. Failures: {failures}")

    selection = select_best_ocr_version(versions)

    selected_version = selection["selected_version"]
    selected_text = selection["selected_text"]

    if not selected_text.strip():
        raise ValueError("Selected OCR text is empty.")

    print("[PIPELINE] OCR selection finished", flush=True)
    selected_text = clean_ocr_text(selected_text)

    # ── Iteration 2: per-box safe correction → final_safe_boxes.json ─────────
    selected_pages = selection.get("selected_pages", [])
    safe_boxes_by_page = []
    for page_entry in selected_pages:
        text_lines = page_entry.get("text_lines", []) if isinstance(page_entry, dict) else []
        safe_boxes_by_page.append(correct_boxes_for_page(text_lines))

    import json as _json
    safe_boxes_path = TEMP_BASE / "final_safe_boxes.json"
    safe_boxes_path.write_text(
        _json.dumps(safe_boxes_by_page, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[PIPELINE] final_safe_boxes.json written ({sum(len(p) for p in safe_boxes_by_page)} boxes)", flush=True)

    # ── Iteration 3: spatial serialization → spatial_chunks.json ─────────────
    spatial_chunks = serialize_safe_boxes(safe_boxes_by_page)
    chunks_path = TEMP_BASE / "spatial_chunks.json"
    chunks_path.write_text(
        _json.dumps(spatial_chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[PIPELINE] spatial_chunks.json written ({len(spatial_chunks)} chunks)", flush=True)

    # ── Iteration 9: rich spatial chunks (pending IDs; patched at confirm-save)
    _c1_pages = [
        {"page": i + 1, "boxes": page_boxes}
        for i, page_boxes in enumerate(safe_boxes_by_page)
    ]
    rich_spatial_chunks = {}
    if _c1_pages:
        try:
            rich_spatial_chunks = _build_rich_spatial_chunks(
                _c1_pages, tenant_id="__pending__", document_id="__pending__"
            )
        except Exception as _e:
            print(f"[PIPELINE] rich spatial chunks failed (non-fatal): {_e}", flush=True)
    # ─────────────────────────────────────────────────────────────────────────

    print("[PIPELINE] Starting LLM correction...", flush=True)
    corrected_text = llm_refine_text(selected_text)
    print("[PIPELINE] LLM correction finished", flush=True)
 
    print("[PIPELINE] Starting structured extraction...", flush=True)
    extracted_json = extract_structured_json_from_text(selected_text)
    print("[PIPELINE] Structured extraction finished", flush=True)

    extracted_json["document_type"] = str(
        extracted_json.get("document_type", "unknown")
    ).strip().lower() or "unknown"

    extracted_json["corrected_text"] = corrected_text
    extracted_json["raw_text"] = selected_text
    extracted_json["ocr_selected_version"] = selected_version
    extracted_json["ocr_scores"] = selection.get("scores", {})
    extracted_json["ocr_failures"] = failures

    extracted_json = correct_extracted_fields(extracted_json)

    arithmetic_validation = validate_arithmetic(extracted_json)
    extracted_json["arithmetic_validation"] = arithmetic_validation
    extracted_json["arithmetic_status"] = arithmetic_validation.get("status", "not_checked")

    # On a mismatch the final total is set to the line items (qty x unit price,
    # plus printed tax). The document stays flagged "mismatch" and the printed
    # value is kept in arithmetic_validation["discrepancies"].
    recommended = arithmetic_validation.get("recommended", {}) or {}
    if extracted_json["arithmetic_status"] == "mismatch":
        if recommended.get("final_total_amount") is not None:
            extracted_json["final_total_amount"] = recommended["final_total_amount"]
        if recommended.get("payable_amount") is not None:
            extracted_json["payable_amount"] = recommended["payable_amount"]

    return {
        "selected_ocr_version": selected_version,
        "selected_ocr_text": selected_text,
        "corrected_text": corrected_text,
        "extracted_fields": extracted_json,
        "ocr_scores": selection.get("scores", {}),
        "ocr_failures": failures,
        "safe_boxes": safe_boxes_by_page,
        "spatial_chunks": spatial_chunks,
        "rich_spatial_chunks": rich_spatial_chunks,
    }

def process_uploaded_document(upload_path: str):
    raw_file = save_uploaded_file(upload_path)
    orig_images = standardize_to_images(raw_file)
    processed_pages = preprocess_images(orig_images)

    if not processed_pages:
        raise ValueError("No processed pages were created.")

    preview = build_preview_from_versions(processed_pages)

    return {
        "uploaded_file": str(raw_file),
        "standard_image": processed_pages[0]["orig"],
        "standard_images": [p["orig"] for p in processed_pages],
        "preprocessed_versions": processed_pages,
        "page_count": len(processed_pages),
        "selected_ocr_version": preview["selected_ocr_version"],
        "selected_ocr_text": preview["selected_ocr_text"],
        "corrected_text": preview["corrected_text"],
        "ocr_scores": preview.get("ocr_scores", {}),
        "ocr_failures": preview.get("ocr_failures", {}),
        "extracted_fields": preview["extracted_fields"],
        # Iteration 9 — forwarded to session for confirm-save embedding
        "safe_boxes": preview.get("safe_boxes", []),
        "rich_spatial_chunks": preview.get("rich_spatial_chunks", {}),
    }