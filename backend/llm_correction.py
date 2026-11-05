import os
import re
from pathlib import Path
from symspellpy import SymSpell, Verbosity
from llm_client import call_pipeline_llm

_CORRECTION_SYSTEM = (
    "You are the most accurate OCR text correction assistant for Sri Lankan financial documents. "
    "You understand Sri Lankan business names, brands, Sinhala Unicode, LKR currency formats, "
    "and common OCR errors in scanned invoices, receipts, and purchase orders. "
    "You fix ONLY genuine OCR typos in common English words — never touch Sinhala, numbers, "
    "company names, product names, or proper nouns. "
    "You output ONLY the corrected text — no explanations, no commentary, nothing else."
)

BASE_DIR = Path(__file__).resolve().parent
DICT_DIR = BASE_DIR / "dictionaries"

sym_spell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)

CUSTOM_ENGLISH_TERMS = set()
CUSTOM_SINHALA_TERMS = set()

ENGLISH_CORRECTIONS = {
    "invioce": "invoice",
    "invoce": "invoice",
    "invoie": "invoice",
    "reciept": "receipt",
    "recipt": "receipt",
    "prnter": "printer",
    "papre": "paper",
    "payble": "payable",
    "toatl": "total",
    "amunt": "amount",
    "devces": "devices",
    "keybaord": "keyboard",
    "quatity": "quantity",
    "quanity": "quantity",
    "suplier": "supplier",
    "custmer": "customer",
    "delivary": "delivery",
    "harecut": "hair cut",
    "haircut": "hair cut",
    "saloon": "salon",
    "ammount": "amount",
    "balnce": "balance",
    "subtoatl": "subtotal",
    "discont": "discount",
    "adress": "address",
    "telepone": "telephone",
    "adresss": "address",
    "expence": "expense",
    "recievable": "receivable",
    "paymant": "payment",
    "totl": "total",
    "transpot": "transport",
    "restaurent": "restaurant",
    "statonary": "stationery",
}

SINHALA_CORRECTIONS = {
    "ඇනවුම": "ඇණවුම",
    "මුලු": "මුළු",
    "ඉන්වොය්ස්": "ඉන්වොයිස්",
    "ඉන්වොයස්": "ඉන්වොයිස්",
    "ඉන්වොයිස": "ඉන්වොයිස්",
    "වටිනාකම්": "වටිනාකම",
    "මුදල": "මුදල්",
    "ලැබියයුතු": "ලැබිය යුතු",
    "ගෙවියයුතු": "ගෙවිය යුතු",
    "ප්රමාණය": "ප්‍රමාණය",
    "මුලු එකතුව": "මුළු එකතුව",
}


def load_custom_terms():
    english_path = DICT_DIR / "english_domain_terms.txt"
    sinhala_path = DICT_DIR / "sinhala_common_5000.txt"

    if english_path.exists():
        with open(english_path, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if word:
                    CUSTOM_ENGLISH_TERMS.add(word.lower())
                    sym_spell.create_dictionary_entry(word.lower(), 10)

    if sinhala_path.exists():
        with open(sinhala_path, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if word:
                    CUSTOM_SINHALA_TERMS.add(word)


load_custom_terms()


def clean_ocr_text(text: str) -> str:
    if not isinstance(text, str):
        return ""

    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[^\S\n]*[\*\|\~`]+[^\S\n]*", " ", text)
    return text.strip()

def count_sinhala_chars(text: str) -> int:
    if not isinstance(text, str):
        return 0
    return len(re.findall(r"[඀-෿]", text))


def looks_like_transliterated_sinhala(original_text: str, corrected_text: str) -> bool:
    """
    If original had Sinhala but corrected lost most Sinhala chars,
    assume the model transliterated/translated incorrectly.
    """
    if not isinstance(original_text, str) or not isinstance(corrected_text, str):
        return False

    original_si = count_sinhala_chars(original_text)
    corrected_si = count_sinhala_chars(corrected_text)

    if original_si >= 8 and corrected_si < max(2, int(original_si * 0.3)):
        return True

    return False

def preserve_sensitive_tokens(text: str):
    if not isinstance(text, str):
        return "", {}

    pattern = r"""
    (
        \b\d[\d,./:-]*\b |
        \b[A-Z]{2,}\d+\b |
        \bDOC\d+\b |
        \bNEW\S*\b |
        \bINV\S*\b |
        \bPO\S*\b |
        \bDN\S*\b |
        \bREC\S*\b |
        \b[A-Z0-9\-_/]{4,}\b |
        \b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b |
        \b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b
    )
    """
    matches = re.findall(pattern, text, flags=re.VERBOSE)

    placeholders = {}
    masked = text

    for i, token in enumerate(matches):
        placeholder = f"__TOKEN_{i}__"
        placeholders[placeholder] = token
        masked = masked.replace(token, placeholder, 1)

    return masked, placeholders


def restore_sensitive_tokens(text: str, placeholders: dict):
    if not isinstance(text, str):
        return ""

    for placeholder, original in placeholders.items():
        text = text.replace(placeholder, original)

    return text

def _sinhala_ratio(text: str) -> float:
    chars = [c for c in text if c.strip()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if "඀" <= c <= "෿") / len(chars)


_PLACEHOLDER_RE = re.compile(r"__TOKEN_\d+__")


def has_correctable_text(masked_text: str) -> bool:
    """True if anything besides masked tokens, digits and punctuation is left.

    When masking hides everything (e.g. an all-caps "LUNCH" becomes
    "__TOKEN_0__"), the LLM gets only a placeholder and answers with a chat
    message ("I don't see any document text...") instead of corrected text.
    There is nothing to correct in that case, so callers skip the LLM.
    """
    if not isinstance(masked_text, str):
        return False
    remainder = _PLACEHOLDER_RE.sub(" ", masked_text)
    return bool(re.search(r"[A-Za-z඀-෿]", remainder))


# Phrases that mean the model replied *about* the task instead of doing it.
_META_REPLY_MARKERS = (
    "i don't see", "i do not see", "i can't see", "i cannot see",
    "please paste", "please provide", "please share", "please send",
    "paste the", "provide the ocr", "the ocr text", "ocr content",
    "placeholder", "i'll return", "i will return", "i'll correct", "i will correct",
    "no text to correct", "nothing to correct", "no document text",
    "i'm sorry", "i am sorry", "sorry,", "as an ai", "could you",
    "it seems", "it looks like", "there is no text", "there's no text",
)


def looks_like_llm_meta_reply(original_text: str, corrected_text: str) -> bool:
    """True if the reply is commentary/a request for input, not corrected text."""
    if not isinstance(corrected_text, str):
        return False
    lower = corrected_text.strip().lower().replace("’", "'")
    original_lower = (original_text or "").lower()
    return any(m in lower and m not in original_lower for m in _META_REPLY_MARKERS)


def looks_like_bad_rewrite(original_text: str, corrected_text: str) -> bool:
    if not isinstance(original_text, str) or not isinstance(corrected_text, str):
        return False

    if looks_like_llm_meta_reply(original_text, corrected_text):
        return True

    original_lines = [ln for ln in original_text.splitlines() if ln.strip()]
    corrected_lines = [ln for ln in corrected_text.splitlines() if ln.strip()]

    # If OCR had multiple lines but corrected output collapsed too much, reject it
    if len(original_lines) >= 5 and len(corrected_lines) <= 2:
        return True

    # Reject if model output starts with chatty phrases (English or Sinhala)
    bad_starts = [
        "i'll correct",
        "here's the corrected",
        "here is the corrected",
        "based on your attempt",
        "please let me know",
        # Sinhala chatty openers
        "මෙය නිවැරදි",   # "this is correct"
        "නිවැරදි කළ",    # "corrected"
        "ඔබගේ",         # "your" — chatty opener
    ]
    lower = corrected_text.strip().lower()
    if any(lower.startswith(x) or corrected_text.strip().startswith(x) for x in bad_starts):
        return True

    # Reject if LLM silently transliterated English input to Sinhala script
    if _sinhala_ratio(corrected_text) > 0.8 and _sinhala_ratio(original_text) < 0.2:
        return True

    return False

def strip_llm_boilerplate(text: str) -> str:
    if not isinstance(text, str):
        return ""

    replacements = [
        "I'll correct the OCR text while following the strict rules. Here's the corrected text:",
        "Here's the corrected text:",
        "Here's the corrected text:",
        "Corrected OCR:",
        "Corrected OCR Text:",
        "Here is the corrected text:",
        "Corrected Text:",
        "Here is the cleaned text:",
        "Here is the corrected OCR text:",
    ]

    for r in replacements:
        text = text.replace(r, "").strip()

    cutoff_markers = [
        "\nNote:",
        "\nExplanation:",
        "\nI only corrected",
        "\nI corrected",
        "\nThis text",
        "\nLet me know",
        "\nBased on your attempt",
        "\nPlease let me know",
        "\nOnce I have the corrected text",
    ]

    for marker in cutoff_markers:
        if marker in text:
            text = text.split(marker)[0].strip()

    return text.strip()


# ── Iteration 2: numeric safeguard + per-box correction ──────────────────────

def extract_digit_sequences(text: str) -> list:
    """Return all digit-only runs in order — used to verify numeric immutability."""
    if not isinstance(text, str):
        return []
    return re.findall(r'\d+', text)


def safe_correct(original: str, corrected: str) -> str:
    """Return corrected text only if digit sequences are unchanged; else return original.

    Research §9.2: a correction that alters any number is always rejected so that
    amounts, dates, IDs, and quantities are never silently modified by the LLM.
    """
    if not isinstance(original, str):
        return original
    if not isinstance(corrected, str):
        return original
    if extract_digit_sequences(original) != extract_digit_sequences(corrected):
        return original
    return corrected


def correct_box(box: dict) -> dict:
    """Apply safe dictionary correction to one OCR text_line box.

    Returns a new dict with the corrected text and provenance fields:
      text          — corrected text (or original if digits would change)
      bbox          — from original box
      confidence    — from original box
      locked_digits — digit sequences extracted from the original text
      source        — 'dict' if dictionary correction changed anything, else 'original'
      polygon       — from original box (pass-through)
    """
    text = box.get("text", "") or ""
    locked_digits = extract_digit_sequences(text)

    dict_corrected = dictionary_correct_text(text)
    final_text = safe_correct(text, dict_corrected)

    return {
        "text": final_text,
        "bbox": box.get("bbox"),
        "confidence": box.get("confidence"),
        "locked_digits": locked_digits,
        "source": "dict" if final_text != text else "original",
        "polygon": box.get("polygon"),
    }


def correct_boxes_for_page(text_lines: list) -> list:
    """Apply box-level safe correction to all text lines of a page."""
    return [correct_box(b) for b in (text_lines or [])]


def _edit_distance(a: str, b: str) -> int:
    n = len(b)
    dp = list(range(n + 1))
    for i, ca in enumerate(a, 1):
        prev, dp[0] = dp[0], i
        for j, cb in enumerate(b, 1):
            prev, dp[j] = dp[j], min(prev + (ca != cb), dp[j] + 1, dp[j - 1] + 1)
    return dp[n]


def compute_cer(original: str, corrected: str) -> float:
    """Character Error Rate = edit_distance / len(original). 0.0 is perfect."""
    if not original:
        return 0.0
    return round(_edit_distance(original, corrected) / len(original), 4)


def compute_nar(boxes: list) -> float:
    """Numeric Accuracy Rate = fraction of boxes where digit sequences are preserved."""
    if not boxes:
        return 1.0
    preserved = sum(
        1 for b in boxes
        if extract_digit_sequences(b.get("text", "")) == b.get("locked_digits", [])
    )
    return round(preserved / len(boxes), 4)


# ─────────────────────────────────────────────────────────────────────────────

def _looks_like_proper_noun(word: str) -> bool:
    """Return True for tokens that should be left uncorrected.

    Covers company names, brand names, abbreviations, and mixed-case identifiers
    that SymSpell would otherwise mangle (e.g. "Keells" → "Keels").
    """
    if not word:
        return False
    # All-caps abbreviations or acronyms (e.g. LKR, PVT, LTD, GST, VAT, PLC)
    if word.isupper() and len(word) >= 2:
        return True
    # Title-case words ≥ 5 chars are likely proper nouns / company names
    if word.istitle() and len(word) >= 5:
        return True
    # CamelCase or mixed-case identifiers (e.g. iPhone, LinkedIn, McDonalds)
    if len(word) >= 3 and not word.islower() and not word.isupper() and not word.istitle():
        return True
    return False


def correct_english_token_with_symspell(word: str) -> str:
    if not word:
        return word

    prefix = ""
    suffix = ""

    while word and not word[0].isalnum():
        prefix += word[0]
        word = word[1:]

    while word and not word[-1].isalnum():
        suffix = word[-1] + suffix
        word = word[:-1]

    if not word:
        return prefix + suffix

    original = word
    lower_word = word.lower()

    # Never correct proper nouns, company names, brand names, or abbreviations
    if _looks_like_proper_noun(original):
        return prefix + original + suffix

    if lower_word in CUSTOM_ENGLISH_TERMS:
        corrected = lower_word
    elif lower_word in ENGLISH_CORRECTIONS:
        corrected = ENGLISH_CORRECTIONS[lower_word]
    else:
        suggestions = sym_spell.lookup(
            lower_word,
            Verbosity.CLOSEST,
            max_edit_distance=2
        )
        # Only accept suggestions with high confidence (≤1 edit for short words)
        if suggestions:
            best = suggestions[0]
            max_dist = 1 if len(lower_word) <= 5 else 2
            corrected = best.term if best.distance <= max_dist else lower_word
        else:
            corrected = lower_word

    if original.istitle():
        corrected = corrected.title()
    elif original.isupper():
        corrected = corrected.upper()

    return prefix + corrected + suffix


def dictionary_correct_text(text: str) -> str:
    if not isinstance(text, str):
        return text

    masked_text, placeholders = preserve_sensitive_tokens(text)

    words = masked_text.split()
    corrected_words = []

    for word in words:
        if re.search(r"[඀-෿]", word):
            new_word = word

            for wrong, correct in SINHALA_CORRECTIONS.items():
                if wrong in new_word:
                    new_word = new_word.replace(wrong, correct)

            corrected_words.append(new_word)
        else:
            corrected_words.append(correct_english_token_with_symspell(word))

    corrected = " ".join(corrected_words)
    corrected = restore_sensitive_tokens(corrected, placeholders)
    return corrected


def correct_via_llm(prompt: str) -> str:
    """Routes OCR-correction calls through the active cloud LLM (Gemini or DeepSeek)."""
    return call_pipeline_llm(prompt, system=_CORRECTION_SYSTEM)


def llm_refine_text(raw_text: str) -> str:
    cleaned = clean_ocr_text(raw_text)
    if not cleaned:
        return ""

    dictionary_fixed = dictionary_correct_text(cleaned)
    masked, placeholders = preserve_sensitive_tokens(dictionary_fixed)

    # Nothing but masked tokens/numbers left -> the LLM has nothing to fix and
    # would only reply with commentary. Keep the text as-is.
    if not has_correctable_text(masked):
        return dictionary_fixed

    prompt =f"""Fix OCR spelling errors in this Sri Lankan financial document text. Return ONLY the corrected text.

STRICT RULES:
1. Fix ONLY clear OCR typos in common English words:
   - toatl → total, invioce → invoice, recept → receipt, Subtoal → Subtotal, payabel → payable
   - Amout → Amount, Discont → Discount, Purchse → Purchase, Delviery → Delivery
2. NEVER change: company names, brand names, shop names, product names, model numbers, proper nouns
3. NEVER change: any number, price, date, phone, ID, reference number, order number
4. NEVER change: ANY Sinhala Unicode text — preserve it character-for-character
5. NEVER translate, transliterate, romanise or explain Sinhala text
6. Keep exact same line order, spacing, and line breaks
7. If unsure whether something is a typo or a proper name — leave it unchanged
8. Return ONLY the corrected text — no labels, no notes, no "Here is:" prefix

EXAMPLES OF CORRECT CORRECTIONS:
  "INVIOCE No: INV-001" → "INVOICE No: INV-001"   (common word fixed)
  "Toatl Amout: 3500"   → "Total Amount: 3500"     (common words fixed)
  "Virtusa (Pvt) Ltd"   → "Virtusa (Pvt) Ltd"      (company name untouched)
  "ශ්‍රී ලංකා"            → "ශ්‍රී ලංකා"               (Sinhala untouched)
  "0112-456789"         → "0112-456789"             (phone untouched)

OCR text to correct:
{masked}"""

    corrected = correct_via_llm(prompt)
    corrected = strip_llm_boilerplate(corrected)
    corrected = restore_sensitive_tokens(corrected, placeholders)

    if not corrected.strip():
        return dictionary_fixed

    if looks_like_bad_rewrite(dictionary_fixed, corrected):
        return dictionary_fixed

    if looks_like_transliterated_sinhala(dictionary_fixed, corrected):
        return dictionary_fixed

    return corrected
