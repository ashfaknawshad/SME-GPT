import os
import requests


# Which preprocessing variants to OCR. Colab runs OCR on every variant sent, so
# fewer variants = proportionally faster. "orig" is always sent (the server
# requires it). Extras default to the binarized "P" (printed-friendly) variant.
# Override with OCR_VARIANTS, e.g. "orig" (fastest, ~3x) or "orig,p_img,m_img" (max accuracy).
_VARIANT_FIELD_TO_KEY = {"p_img": "P", "m_img": "M"}


def _selected_extra_variants() -> list[str]:
    raw = os.getenv("OCR_VARIANTS", "orig,p_img")
    fields = {v.strip().lower() for v in raw.split(",") if v.strip()}
    # orig is mandatory and handled separately; keep only valid extras, preserve order
    return [f for f in ("p_img", "m_img") if f in fields]


def colab_base_urls(value: str | None = None) -> list[str]:
    """COLAB_OCR_URL may list several OCR workers, comma-separated (e.g. a Colab
    and a Kaggle notebook), tried in order: when one notebook's runtime ends,
    requests fail over to the next instead of failing the upload."""
    raw = value if value else os.getenv("COLAB_OCR_URL", "")
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


def send_images_to_colab_ocr(processed_pages: list[dict], colab_url: str = None) -> dict:
    base_urls = colab_base_urls(colab_url)

    if not base_urls:
        raise ValueError("COLAB_OCR_URL is empty. Please set it before running the backend.")

    extra_variants = _selected_extra_variants()
    print(
        f"[COLAB-OCR] sending variants per page: {['orig'] + extra_variants} "
        f"({len(extra_variants) + 1} OCR pass(es)/page)",
        flush=True,
    )

    opened_files = []
    multipart_files = []

    try:
        for idx, page in enumerate(processed_pages, start=1):
            orig_file = open(page["orig"], "rb")
            opened_files.append(orig_file)
            multipart_files.append(("orig", (f"page_{idx:03d}_orig.png", orig_file, "image/png")))

            for field in extra_variants:
                variant_key = _VARIANT_FIELD_TO_KEY[field]
                vfile = open(page[variant_key], "rb")
                opened_files.append(vfile)
                multipart_files.append(
                    (field, (f"page_{idx:03d}_{field}.png", vfile, "image/png"))
                )

        errors = []
        for base in base_urls:
            # A failed attempt may have consumed the uploads; rewind before retrying.
            for f in opened_files:
                f.seek(0)
            try:
                response = requests.post(
                    base + "/ocr",
                    files=multipart_files,
                    timeout=600,
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.Timeout:
                errors.append(f"{base}: Colab OCR API timed out.")
            except requests.exceptions.ConnectionError as e:
                errors.append(f"{base}: Could not connect to Colab OCR API: {e}")
            except requests.exceptions.HTTPError as e:
                # A stopped ngrok tunnel answers with an HTTP error page.
                errors.append(f"{base}: HTTP error from Colab OCR API: {e}\nResponse body: {response.text[:500]}")
            if base != base_urls[-1]:
                print(f"[COLAB-OCR] {base} unavailable, trying next worker", flush=True)

        raise Exception("\n".join(errors))
    finally:
        for f in opened_files:
            try:
                f.close()
            except Exception:
                pass