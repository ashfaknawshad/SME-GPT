"""Guardrails for the agent: grounding of the final answer + a request filter.

Generalizes pal_answer.py's single-plan grounding check to a multi-tool-call
agent turn: the LLM may only restate a monetary/numeric figure that a tool
call actually returned. Anything else means the LLM computed or invented a
number, which the architecture forbids end-to-end (the LLM plans by calling
deterministic tools -- pal_executor/pal_validator do the math --
docs/components/component-3.md "Why"). This is the last line of defense,
mirrored from pal_answer._collect_allowed_numbers / _answer_is_grounded.

is_blocked_request() is the input side: prompt-injection / system-prompt /
cross-tenant requests get a fixed refusal without the LLM ever seeing them.
"""
from __future__ import annotations

import json
import re

_MONEY_TOKEN_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{4,}")

# Every standalone number, including small ones ("LKR 500", "18%", "45 invoices").
# Digits glued to letters or hyphens are identifiers (PO8, INV-2045), not figures.
_ANY_NUMBER_RE = re.compile(r"(?<![\w.,\-/])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w/])|(?<![\w.,\-/])\d+(?:\.\d+)?(?![\w/])")

_DATE_RE = re.compile(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[*\-]\s*)?\d{1,2}[.)]\s", re.MULTILINE)
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_DAY_MONTH_RE = re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\b|\b{_MONTHS}\s+\d{{1,2}}\b", re.IGNORECASE)
_DURATION_RE = re.compile(
    r"\b\d+\s*-?\s*(?:days?|weeks?|months?|years?|hours?|minutes?|steps?|tips?|ways?|items? on this list)\b",
    re.IGNORECASE,
)
_CURRENCY_BEFORE_RE = re.compile(r"(?:lkr|rs\.?|usd|eur|gbp|sgd|\$|€|£|රු\.?|රුපියල්)\s*-?$", re.IGNORECASE)
_CURRENCY_AFTER_RE = re.compile(r"^\s*(?:lkr|usd|eur|gbp|rupees?|/=|/-|%|percent|රුපියල්|රු)", re.IGNORECASE)

# A bare small integer with no currency or % marker reads as a count ("3 steps",
# "2 suppliers"), and blanking whole answers over those would do more harm than
# good. Anything larger, or marked as money or a percentage, must be grounded.
_MAX_UNMARKED_INT = 12

# Pairwise sums/differences are only meaningful over a handful of figures. Over
# a large result nearly any number is reachable, which would defeat the guard.
_MAX_ARITHMETIC_BASE = 40

_ROUNDING_TOLERANCE = 0.5

# "roughly LKR 384,000" for 384,407.08 restates a real figure. A round number is
# accepted only if it's that figure correctly rounded and within this share of it.
_MAX_ROUNDING_SHARE = 0.01

# A tool's headline results. Their sums and differences (a net position from two
# totals) stay allowed however many other figures the conversation holds.
_HEADLINE_KEYS = {"value", "base_value", "difference", "profit", "profit_excluding_tax", "revenue", "expenses"}

_BLOCKED_REQUEST_RES = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instruction|prompt|rule|guardrail|guard|polic)",
    r"\b(show|reveal|print|repeat|tell me|what (is|are)|give me)\b.{0,30}\b(system prompt|your (prompt|instructions|rules))\b",
    r"\b(jailbreak|developer mode|dan mode)\b",
    r"\b(other|another|different)\s+(users?|tenants?|accounts?)\b.{0,40}\b(data|documents?|invoices?|records?|figures?)\b",
    r"\b(data|documents?|invoices?|records?|figures?)\b.{0,40}\b(other|another|different)\s+(users?|tenants?|accounts?)\b",
    r"\b(drop|truncate)\s+table\b|\bdelete\s+from\b|\bunion\s+select\b",
)]


def is_blocked_request(text: str) -> bool:
    """True for prompt-injection, system-prompt or cross-tenant requests."""
    return any(p.search(text or "") for p in _BLOCKED_REQUEST_RES)


def content_text(content) -> str:
    """Plain text of a message's content. Some providers (Gemini) return a list
    of content blocks rather than a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return "" if content is None else str(content)


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _collect_numbers(value, out: set[float]) -> None:
    if _is_number(value):
        # An answer writes a loss of -500 as "500" or "-500" alike.
        if value == value:  # skip NaN
            out.add(round(abs(float(value)), 2))
    elif isinstance(value, str):
        # Numbers embedded in a tool's TEXT output are grounded too — e.g. a
        # figure quoted verbatim from a retrieved document chunk. Without this,
        # answering "show me that document" from search_documents results (which
        # are text, not structured numbers) got wrongly flagged as unverified.
        for token in _ANY_NUMBER_RE.findall(value) + _MONEY_TOKEN_RE.findall(value):
            try:
                out.add(round(float(token.replace(",", "")), 2))
            except ValueError:
                pass
    elif isinstance(value, dict):
        for v in value.values():
            _collect_numbers(v, out)
    elif isinstance(value, list):
        for v in value:
            _collect_numbers(v, out)


def _collect_headlines(value, out: set[float]) -> None:
    if isinstance(value, dict):
        for key, v in value.items():
            if key in _HEADLINE_KEYS and _is_number(v) and v == v:
                out.add(round(abs(float(v)), 2))
            else:
                _collect_headlines(v, out)
    elif isinstance(value, list):
        for v in value:
            _collect_headlines(v, out)


def _pairwise(values: set[float]) -> set[float]:
    combined = set()
    ordered = list(values)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            combined.add(round(a + b, 2))
            combined.add(round(abs(a - b), 2))
    return combined


def _expand_with_arithmetic(base: set[float]) -> set[float]:
    """Add simple pairwise sums/differences of allowed numbers, so a legitimate
    derived total (e.g. "surplus = income - expenses", both real/user-given
    numbers) isn't blocked just because the sum itself wasn't independently
    verified. Still refuses any figure not traceable back to a real number the
    agent was actually given -- this only expands what combinations of THOSE
    numbers are allowed, it doesn't invent new base numbers."""
    if len(base) > _MAX_ARITHMETIC_BASE:
        return set(base)
    return set(base) | _pairwise(base)


def collect_allowed_numbers(tool_outputs: list, human_texts: list | None = None) -> set[float]:
    """Every number present anywhere in this turn's tool results, PLUS any
    numbers the user themselves typed this turn (e.g. planning a budget with
    hypothetical figures -- "I expect 300000 income and 220000 expenses" isn't
    something a tool can verify, but it isn't a hallucination either since the
    user supplied it). tool_outputs are ToolMessage.content values -- usually
    dicts, sometimes JSON strings. Expanded with simple sums/differences so
    basic arithmetic on these numbers (a total, a surplus) isn't blocked."""
    allowed: set[float] = set()
    headlines: set[float] = set()
    for content in tool_outputs:
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (ValueError, TypeError):
                pass  # not JSON — scan the raw text for numbers (handled below)
        _collect_numbers(content, allowed)
        _collect_headlines(content, headlines)
    for text in human_texts or []:
        _collect_numbers(content_text(text), allowed)
    return _expand_with_arithmetic(allowed) | headlines | _pairwise(headlines)


def _blank_exempt_spans(text: str) -> str:
    """Blank out numbers that aren't financial claims: dates, list markers and
    durations. Same length is kept so match positions stay meaningful."""
    for pattern in (_DATE_RE, _LIST_MARKER_RE, _DAY_MONTH_RE, _DURATION_RE):
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _must_be_grounded(token: str, before: str, after: str) -> bool:
    num = float(token.replace(",", ""))
    plain_int = "," not in token and "." not in token
    marked = bool(_CURRENCY_BEFORE_RE.search(before) or _CURRENCY_AFTER_RE.search(after))
    if plain_int and not marked and 1900 <= num <= 2100:
        return False  # a year, not a monetary figure
    if plain_int and not marked and num <= _MAX_UNMARKED_INT:
        return False  # a small count, see _MAX_UNMARKED_INT
    return True


def _is_rounding_of_allowed(token: str, num: float, allowed: set[float]) -> bool:
    """True if a round whole number ("384,000") is a real figure correctly rounded."""
    digits = token.replace(",", "")
    if "." in digits or num == 0:
        return False
    trailing_zeros = len(digits) - len(digits.rstrip("0"))
    if not trailing_zeros:
        return False
    precision = 10 ** trailing_zeros
    return any(
        a and round(a / precision) * precision == num and abs(num - a) <= abs(a) * _MAX_ROUNDING_SHARE
        for a in allowed
    )


def ungrounded_figures(text: str, allowed: set[float]) -> list[str]:
    """The figures in `text` that no tool call produced."""
    scrubbed = _blank_exempt_spans(content_text(text))
    rejected = []
    for match in _ANY_NUMBER_RE.finditer(scrubbed):
        token = match.group(0)
        before = scrubbed[max(0, match.start() - 12):match.start()]
        after = scrubbed[match.end():match.end() + 12]
        if not _must_be_grounded(token, before, after):
            continue
        num = float(token.replace(",", ""))
        if any(abs(num - a) <= _ROUNDING_TOLERANCE for a in allowed):
            continue
        if not _is_rounding_of_allowed(token, num, allowed):
            rejected.append(token)
    return rejected


def answer_is_grounded(text: str, allowed: set[float]) -> bool:
    """False if `text` states a number no tool call produced."""
    return not ungrounded_figures(text, allowed)
