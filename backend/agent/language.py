"""Per-message reply-language detection for the conversational agent.

The chat must answer in the language of the *latest* question, independent of
the app's UI language toggle and of earlier turns:

    Sinhala script ("මට ගෙවිය යුතු මුදල කීයද?")     -> "si"        reply in Sinhala script
    Singlish ("mata gewanna thiyena salli kiyada?") -> "singlish"  reply in Sinhala, Latin letters
    English ("How much do I owe?")                   -> "en"        reply in English

Leaving it to the model ("answer in the user's language") is unreliable: it
drifts to the language of earlier turns, and has no idea Singlish is a thing it
should mirror. So we decide deterministically here and tell the model exactly
what to do for this turn (see reply_language_instruction).
"""
from __future__ import annotations

import re

_SINHALA_CHAR = re.compile(r"[඀-෿]")
_LATIN_CHAR = re.compile(r"[A-Za-z]")
_WORD = re.compile(r"[A-Za-z]+")

# Common romanized-Sinhala words in everyday business chat. Chosen to be
# distinctive: plain-English words that merely look similar ("one", "api",
# "hari") are deliberately left out so English questions never trip this.
_SINGLISH_STRONG = {
    # pronouns / people
    "mata", "mage", "mama", "magee", "oya", "oyage", "oyata", "eyala", "eyage", "apita", "apige",
    "mokakda", "mokadda", "monawada", "monwada", "mokada", "kawda", "kauda", "koheda", "kohomada",
    "kiyada", "kiyak", "kiyakda", "keeyada", "kiyadha", "kochchara", "kochcharada", "kawadda",
    # verbs / auxiliaries
    "thiyenawa", "thiyanawa", "tiyenawa", "thiyena", "tiyena", "thiyana", "thibba", "thiyeda",
    "thiyenawada", "tiyenawada", "nadda", "naddha", "nathi", "natho", "naha", "nehe",
    "karanna", "karala", "kalaa", "karapu", "karanawa", "karannada", "kalada",
    "denna", "dennako", "dunna", "dunnada", "deela", "ganna", "gaththa", "gattha", "gannawa",
    "gewanna", "gewala", "gewwa", "gewapu", "gewuwa", "gewiya", "gewannada", "gewanawa",
    "labenna", "labuna", "labunada", "labila", "labiya", "labena",
    "balanna", "balala", "kiyanna", "kiyala", "kiyapu", "hoyanna", "hoyala", "pennanna",
    "puluwanda", "puluwan", "puluwand", "oni", "ona", "onee", "oneda", "wenna", "una", "unada",
    "wenawa", "wenne", "wela", "welada",
    # nouns / business words
    "salli", "ganan", "gana", "mudal", "mudala", "wiyadam", "aadayama", "adayama", "labaya",
    "laabaya", "paduwa", "ithuru", "naya", "ayawaya", "masa", "mase", "maase", "awurudda",
    "adha", "ada", "iye", "heta", "dan", "dang", "thawa", "tawa", "okkoma", "hama",
    # particles / connectors
    "ekak", "eka", "ekata", "eke", "eken", "ewa", "meka", "mekata", "eeka", "ehema", "mehema",
    "neda", "needa", "nedda", "nisa", "walata", "wala", "gena", "gane", "gaana",
}
# Short, ambiguous tokens: only count when other evidence is present.
_SINGLISH_WEAK = {"da", "de", "ne", "nam", "ta", "wa", "ko", "ai", "hari", "harida"}
# Morphological endings of romanized Sinhala ("supplierta", "invoicekata").
_SINGLISH_SUFFIX = re.compile(r"(ekata|eken|ekak|ekema|walata|walin|ingen|kenek|lage|lata|ta)$")


def detect_reply_language(text: str) -> str:
    """Return "si", "singlish" or "en" for one user message."""
    if not text:
        return "en"
    sinhala = len(_SINHALA_CHAR.findall(text))
    latin = len(_LATIN_CHAR.findall(text))
    # Any real share of Sinhala script -> answer in Sinhala script. Mixed
    # messages ("මගේ invoice total එක කීයද?") are Sinhala sentences that happen
    # to use English business words.
    if sinhala and sinhala >= 0.2 * (sinhala + latin):
        return "si"

    words = [w.lower() for w in _WORD.findall(text)]
    if not words:
        return "si" if sinhala else "en"
    strong = sum(1 for w in words if w in _SINGLISH_STRONG)
    weak = sum(1 for w in words if w in _SINGLISH_WEAK)
    suffixed = sum(
        1 for w in words
        if w not in _SINGLISH_STRONG and len(w) > 5 and _SINGLISH_SUFFIX.search(w)
    )
    score = strong + 0.5 * suffixed + (0.5 * weak if strong else 0)
    if score >= 2 or (strong >= 1 and score / len(words) >= 0.25):
        return "singlish"
    return "en"


_INSTRUCTIONS = {
    "en": (
        "REPLY LANGUAGE FOR THIS TURN: English. The user's latest message is in English, so answer "
        "in clear English -- even if earlier messages in this conversation were in Sinhala or Singlish."
    ),
    "si": (
        "REPLY LANGUAGE FOR THIS TURN: Sinhala (සිංහල අකුරු). The user's latest message is in Sinhala, "
        "so answer in natural, polite Sinhala script -- even if the app or earlier messages were in "
        "English. Common business words (invoice, PO, receipt, supplier) may stay in English where "
        "Sri Lankans normally say them that way. Keep amounts, 'LKR', document IDs and "
        "company/supplier names exactly as the tools returned them."
    ),
    "singlish": (
        "REPLY LANGUAGE FOR THIS TURN: Singlish -- Sinhala written in English (Latin) letters, the way "
        "Sri Lankans type in chat. The user's latest message is in Singlish, so reply ONLY in Singlish: "
        "do NOT use Sinhala script and do NOT switch to plain English sentences. Example style: "
        "\"Oyage customerslagen thawa LKR 360,000 labenna thiyenawa. Wadiyenma thiyenne ABC Traders "
        "gen.\" Keep amounts, 'LKR', document IDs and company/supplier names exactly as the tools "
        "returned them."
    ),
}


def reply_language_instruction(lang: str) -> str:
    return _INSTRUCTIONS.get(lang, _INSTRUCTIONS["en"])


# The grounding guard's safe fallbacks (agent/graph.py), in each reply language.
GUARD_FALLBACK = {
    "en": (
        "I pulled the figures but couldn't fully verify the answer I was about to give, so I'd "
        "rather not state a number I'm unsure of. You can see exactly what my tools returned under "
        "“How I worked this out” below — or ask me to try again."
    ),
    "si": (
        "මම අදාළ සංඛ්‍යා ලබා ගත්තා, නමුත් මා දීමට ගිය පිළිතුර සම්පූර්ණයෙන් තහවුරු කළ නොහැකි වූ නිසා, "
        "විශ්වාස නැති අගයක් සඳහන් කිරීමට මා කැමති නැහැ. මගේ මෙවලම් ලබා දුන් දේ පහත "
        "“How I worked this out” යටතේ බලන්න — නැතහොත් නැවත උත්සාහ කරන්න කියන්න."
    ),
    "singlish": (
        "Mama figures tika gaththa, eth mama denna giya answer eka hariyatama verify karanna bari "
        "una nisa, sure nathi ganan ekak kiyanna mama kamathi naha. Mage tools denna dewal pahala "
        "“How I worked this out” eke balanna — nathnam aye try karanna kiyanna."
    ),
}
GUARD_FALLBACK_NO_DATA = {
    "en": "I couldn't ground that answer in verified data — could you rephrase your question?",
    "si": "එම පිළිතුර තහවුරු කළ දත්ත මත පදනම් කළ නොහැකි විය — කරුණාකර ඔබේ ප්‍රශ්නය වෙනත් ආකාරයකින් අසන්න.",
    "singlish": "E answer eka verify karapu data walin confirm karanna bari una — prashne wenas widihakata ahanna puluwanda?",
}
