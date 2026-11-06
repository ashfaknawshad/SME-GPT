import json
import re
import calendar
import difflib
import pandas as pd
from datetime import date, timedelta, datetime

from dataset_manager import load_main_dataset

# Label used in query results' "source_file" field (no longer a file path).
DATASET_PATH = "financial_documents (postgres)"


def load_dataset(user_id: str = None):
    # Reads from Postgres (Supabase) via the data-access layer; tenant-scoped
    # when user_id is provided.
    df = load_main_dataset(user_id=user_id)
    return enrich_dataset(df)


def safe_json_load(value):
    if not value or str(value).strip() in ["", "NULL", "null", "None"]:
        return {}

    try:
        return json.loads(value)
    except Exception:
        return {}


def normalize_text(value):
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def to_float(value):
    if value is None:
        return 0.0

    text = str(value).strip()
    if text == "" or text.upper() == "NULL":
        return 0.0

    text = (
        text.replace(",", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .replace("LKR", "")
        .replace("$", "")
        .strip()
    )

    try:
        return float(text)
    except Exception:
        return 0.0

def preferred_amount(row):
    # Query logic should prioritize final_total_amount
    amount = to_float(row.get("final_total_amount", 0))
    if amount == 0.0:
        amount = to_float(row.get("payable_amount", 0))
    if amount == 0.0:
        amount = to_float(row.get("raw_total_amount", 0))
    return amount


def document_total(row):
    """What a document is actually worth: its final total, after discounts and
    including VAT. Only when no total was saved is it rebuilt from the items."""
    amount = preferred_amount(row)
    if amount != 0.0:
        return amount
    lines = 0.0
    for item in extract_items_from_row(row):
        line = to_float(item.get("line_total")) or to_float(item.get("quantity")) * to_float(item.get("unit_price"))
        lines += line
    if lines == 0.0:
        return 0.0
    return round(lines - to_float(row.get("discount_amount")) + to_float(row.get("tax_amount")), 2)


def _amount_with_structured_fallback(df, structured, field):
    """A figure from its DB column, else from structured_json. Documents saved
    before tax was persisted only have it there, and discount has no column."""
    column = df[field] if field in df.columns else pd.Series([None] * len(df), index=df.index)
    values = []
    for value, fallback in zip(column, structured):
        amount = to_float(value)
        values.append(amount if amount else abs(to_float(fallback.get(field))))
    return values


def extract_items_from_row(row):
    items = row.get("items", [])

    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []

    if not isinstance(items, list):
        return []

    cleaned_items = []
    for item in items:
        if not isinstance(item, dict):
            continue

        cleaned_items.append({
            "description": item.get("description", "NULL"),
            "quantity": item.get("quantity", "NULL"),
            "unit_price": item.get("unit_price", "NULL"),
            "line_total": item.get("line_total", "NULL"),
        })

    return cleaned_items
def enrich_dataset(df: pd.DataFrame):
    if df.empty:
        for col in ["order_id", "flow_type", "received_status", "paid_status", "items"]:
            df[col] = []
        return df

    structured = (
        df["structured_json"].apply(safe_json_load)
        if "structured_json" in df.columns
        else pd.Series([{}] * len(df))
    )

    df = df.copy()
    df["order_id"] = structured.apply(lambda x: x.get("order_id", "NULL"))

    if "effective_flow_type" in df.columns:
        df["flow_type"] = df["effective_flow_type"].apply(
            lambda x: x if str(x).strip() not in ["", "NULL", "null", "None"] else None
        )
        df["flow_type"] = df["flow_type"].fillna(
            structured.apply(lambda x: x.get("flow_type", "unknown"))
        )
    else:
        df["flow_type"] = structured.apply(lambda x: x.get("flow_type", "unknown"))

    df["received_status"] = structured.apply(lambda x: x.get("received_status", "NULL"))
    df["paid_status"] = structured.apply(lambda x: x.get("paid_status", "NULL"))
    df["items"] = structured.apply(lambda x: x.get("items", []))
    df["tax_amount"] = _amount_with_structured_fallback(df, structured, "tax_amount")
    df["tax_rate"] = _amount_with_structured_fallback(df, structured, "tax_rate")
    df["discount_amount"] = _amount_with_structured_fallback(df, structured, "discount_amount")

    numeric_cols = ["raw_total_amount", "final_total_amount", "payable_amount"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].apply(to_float)

    for text_col in ["company_name", "supplier_name", "document_type", "date", "document_id"]:
        if text_col not in df.columns:
            df[text_col] = ""

    return df


def filter_user_context(df: pd.DataFrame, user_id: str):
    if "user_id" not in df.columns:
        return df.iloc[0:0].copy()

    return df[df["user_id"].astype(str) == str(user_id)].copy()


# ── Financial advisor support ────────────────────────────────────────────────
# Note: the agentic engine (backend/agent/) handles advisory-style questions
# via its general tool-calling loop instead of a dedicated snapshot function.
# This path serves the same purpose for the still-default /ask-query engine
# (pal_qa.py), which has no equivalent grounded-advice capability of its own.

_ADVICE_TRIGGER_PHRASES = (
    "how can i", "how do i", "how should i", "how can we", "how do we", "how should we",
    "how to", "any tips", "give me tips", "give me advice", "any advice", "recommend",
    "what should i do", "what can i do", "best way to", "bring down", "cut down",
    "improve my", "improve our", "manage my", "manage our", "reduce my", "reduce our",
    "lower my", "lower our", "strategy for", "strategies for",
    # Sinhala equivalents (common phrasing for advice-seeking)
    "කොහොමද අඩු කරන්නේ", "කළමනාකරණය කරන්නේ කොහොමද", "උපදෙස්", "වැඩිදියුණු කරන්නේ",
)

_ADVICE_FINANCE_NOUNS = (
    "budget", "receivable", "payable", "cash flow", "cashflow", "expense", "expenses",
    "debt", "spending", "spend", "saving", "savings", "profit", "revenue", "finance",
    "finances", "financial", "invoice", "invoices", "payment", "payments",
    "අයවැය", "ලැබිය යුතු", "ගෙවිය යුතු", "වියදම",
)

# Planning requests ("help me plan next month") are advice-seeking on their own,
# without needing a separate finance-noun match — "plan a month" doesn't contain
# any of the nouns above but is unambiguously a planning request in this app.
_PLANNING_TRIGGER_PHRASES = (
    "help me plan", "help plan", "plan for", "planning for", "let's plan", "lets plan",
    "make a plan", "create a plan", "financial plan", "budget plan", "help me budget",
    "plan my", "plan next month", "plan for next month", "help me manage",
    "සැලසුම් කරන්න", "මාසේ සැලසුම",
)


def _is_advice_question(normalized_q: str) -> bool:
    """True for advice-seeking questions ("how can I manage my budget") as opposed
    to factual lookups ("what is my total receivable"). Liberal on purpose: a false
    positive still gets answered correctly by the advisor (which also reports the
    real numbers), unlike misrouting a factual question into the arithmetic engine."""
    q = normalized_q.lower()
    if any(p in q for p in _PLANNING_TRIGGER_PHRASES):
        return True
    has_trigger = any(p in q for p in _ADVICE_TRIGGER_PHRASES)
    has_finance_noun = any(n in q for n in _ADVICE_FINANCE_NOUNS)
    return has_trigger and has_finance_noun


def get_financial_snapshot(user_id: str, company_name: str) -> dict:
    """Live financial snapshot for the advisor to ground its answer in real numbers:
    totals, top open receivables/payables, this-month-vs-last-month net trend, and
    the user's saved budget targets."""
    df = load_dataset(user_id=user_id)
    df = filter_user_context(df, user_id=user_id)
    if company_name:
        cn = normalize_text(company_name)
        df = df[df["company_name"].apply(lambda v: normalize_text(v) == cn)]

    total_receivable = 0.0
    total_payable = 0.0
    open_receivables = []
    open_payables = []

    for _, row in df.iterrows():
        flow = normalize_flow(row.get("flow_type"))
        amount = preferred_amount(row)
        paid = str(row.get("paid_status", "")).strip().lower()
        received = str(row.get("received_status", "")).strip().lower()
        entry = {
            "document_id": row.get("document_id", "NULL"),
            "supplier_name": row.get("supplier_name", "NULL"),
            "amount": amount,
            "date": row.get("date", "NULL"),
        }
        if flow == "receivable" and received != "received":
            total_receivable += amount
            open_receivables.append(entry)
        elif flow == "payable" and paid != "paid":
            total_payable += amount
            open_payables.append(entry)

    open_receivables = sorted(open_receivables, key=lambda x: -x["amount"])[:5]
    open_payables = sorted(open_payables, key=lambda x: -x["amount"])[:5]

    # This-month vs last-month net (income minus expense), for trend context.
    today = date.today()
    this_month = today.strftime("%Y-%m")
    last_month_date = (today.replace(day=1) - timedelta(days=1))
    last_month = last_month_date.strftime("%Y-%m")

    def _month_net(month_key: str) -> float:
        net = 0.0
        for _, row in df.iterrows():
            d = _parse_date_for_filter(row.get("date"))
            if d is None or d.strftime("%Y-%m") != month_key:
                continue
            flow = normalize_flow(row.get("flow_type"))
            amount = preferred_amount(row)
            if flow in ("receivable", "cash_inflow"):
                net += amount
            elif flow in ("payable", "cash_outflow"):
                net -= amount
        return round(net, 2)

    net_this_month = _month_net(this_month)
    net_last_month = _month_net(last_month)

    budgets = {}
    try:
        from db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT content FROM "ActivityLog" WHERE "userId"=%s AND type=%s '
                    'ORDER BY "createdAt" DESC LIMIT 1',
                    (str(user_id), "BUDGET_SETTINGS")
                )
                row = cur.fetchone()
        if row:
            content = row.get("content") if isinstance(row, dict) else row[0]
            budgets = json.loads(content) if content else {}
    except Exception:
        budgets = {}

    return {
        "total_receivable_amount": round(total_receivable, 2),
        "total_payable_amount": round(total_payable, 2),
        "open_receivables": open_receivables,
        "open_payables": open_payables,
        "net_this_month": net_this_month,
        "net_last_month": net_last_month,
        "budgets": budgets,
        "document_count": len(df),
    }


def filter_company_context(df: pd.DataFrame, company_name: str):
    target = normalize_text(company_name)
    if not target:
        return df.iloc[0:0].copy()

    return df[
        df["company_name"].apply(normalize_text).str.contains(target, na=False)
        | df["supplier_name"].apply(normalize_text).str.contains(target, na=False)
    ].copy()


# ── Fuzzy spell correction for query terms ────────────────────────────────────
# Financial domain vocabulary that users commonly mistype.
_FINANCIAL_VOCAB = [
    "payables", "receivables", "invoices", "invoice", "receipts", "receipt",
    "suppliers", "supplier", "customers", "customer", "payments", "payment",
    "purchase", "orders", "delivery", "deliveries", "balance", "outstanding",
    "overdue", "pending", "approved", "rejected", "cancelled", "fulfilled",
    "expenses", "revenue", "income", "profit", "summary", "history",
    "partially", "delivered", "transactions", "statement", "amount",
    "total", "latest", "active", "inactive", "contact", "details",
    "monthly", "weekly", "annually", "comparison", "today", "yesterday",
    "payable", "receivable", "purchase order",
]

# Words to skip correction (short words, numbers, known Sinhala)
_SKIP_CORRECTION = {"po", "dn", "in", "of", "by", "to", "is", "a", "an", "the",
                    "me", "my", "us", "we", "do", "i", "and", "or", "for", "rs",
                    "lkr", "all", "any", "no", "not", "how", "who", "what",
                    "when", "which", "show", "find", "get", "list",
                    # Common temporal / generic words — don't correct these
                    "month", "week", "year", "day", "today", "from", "this",
                    "last", "next", "are", "was", "were", "have", "has", "been",
                    "with", "that", "their", "its", "than", "more", "less",
                    "above", "below", "over", "under", "between", "still", "yet",
                    # Real English words that fuzzy-match a vocab term and get
                    # wrongly "corrected" (e.g. supplies→suppliers), especially
                    # when they appear in entity names.
                    "supplies", "supply", "services", "service", "solutions",
                    "holdings", "traders", "trading", "stores", "store"}


# Double-quoted spans (straight or curly quotes) are treated as literal
# entity names the user typed deliberately -- e.g. a supplier called
# "Lanka Beverage Supplies". Correcting inside them turns "Supplies" into
# "Suppliers" and derails routing, so we mask these spans out entirely.
_QUOTED_SPAN_RE = re.compile(r'"[^"]*"|“[^”]*”')


def spell_correct_query(question: str) -> tuple[str, list[str]]:
    """
    Correct common financial-term typos in a query using fuzzy matching.
    Returns (corrected_question, list_of_corrections_made).
    Example: "show my payahles" -> ("show my payables", ["payahles → payables"])

    Text inside double quotes is left untouched -- users quote entity names
    (supplier/customer) precisely so they are not "corrected" into vocab words.
    """
    corrections = []

    # Mask quoted literals so the tokenizer never touches them, restore after.
    protected: list[str] = []

    def _stash(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    masked = _QUOTED_SPAN_RE.sub(_stash, question)

    words = masked.split()
    corrected_words = []

    for word in words:
        # Preserve punctuation suffix (e.g., "payahles?" -> check "payahles")
        suffix = ""
        clean = word
        if word and word[-1] in "?.!,":
            suffix = word[-1]
            clean = word[:-1]

        lower = clean.lower()

        # Skip: already correct, too short, numeric, skip-list, Sinhala
        if (lower in _SKIP_CORRECTION
                or len(lower) < 4
                or lower.isdigit()
                or re.search(r"[඀-෿]", lower)):
            corrected_words.append(word)
            continue

        # Check if already a valid financial term (exact match)
        if lower in _FINANCIAL_VOCAB:
            corrected_words.append(word)
            continue

        # Fuzzy match: only suggest if similarity >= 0.82
        matches = difflib.get_close_matches(lower, _FINANCIAL_VOCAB, n=1, cutoff=0.82)
        if matches and matches[0] != lower:
            corrected = matches[0]
            # Preserve original capitalisation style
            if clean[0].isupper():
                corrected = corrected.capitalize()
            corrections.append(f"{clean} → {corrected}")
            corrected_words.append(corrected + suffix)
        else:
            corrected_words.append(word)

    result = " ".join(corrected_words)
    # Restore the quoted literals we masked out above.
    for idx, original in enumerate(protected):
        result = result.replace(f"\x00{idx}\x00", original)
    return result, corrections


_SINHALA_VERB_MAP = {
    # Action verbs → English equivalents for intent detection
    "පෙන්නන්න": "show",
    "හොයන්න": "find",
    "දෙන්න": "show",
    "කියන්න": "show",
    "open කරන්න": "find",
    # Status adjectives
    "ගෙවලාද": "paid",
    "ගෙවලා නැති": "not paid",
    "ගෙවලා නැත": "not paid",
    "deliver නොවූ": "not delivered",
    "reject කරපු": "rejected",
    "approve කළේ": "approved by",
    "approve කළා": "approved",
    "cancel කරපු": "cancelled",
    "sign නොකරපු": "unsigned",
    # Time expressions
    "මේ මාසේ": "this month",
    "ගිය මාසේ": "last month",
    "අද": "today",
    "ඊයේ": "yesterday",
    "මේ සතියේ": "this week",
    "ගිය සතියේ": "last week",
    "මේ අවුරුද්දේ": "this year",
    "ගිය අවුරුද්දේ": "last year",
    # Document types
    "ඉන්වොයිස්": "invoices",
    "රිසිට්": "receipts",
    "බෙදාහැරීම": "delivery notes",
    "ඇණවුම": "purchase orders",
    # Entities
    "සැපයුම්කරු": "supplier",
    "ගනුදෙනුකරු": "customer",
    # Quantity/count
    "කීයක්": "how many",
    "කීයද": "how much",
    "කීය": "how many",
    # Common adjectives
    "වැඩියෙන්ම": "highest",
    "අලුත්ම": "latest",
    "හැදූ": "created",
    # Status words
    "pending": "pending",
    "open තියෙන": "open",
    "partially delivered": "partially delivered",
    "overdue": "overdue",
    "ලැබිය යුතු": "receivable",
    "ගෙවිය යුතු": "payable",
    # Finance nouns (totals/aggregates) — closes the gap where a question like
    # "මගේ මුළු ලැබීම් කීයද?" (what is my total receivable?) had no English
    # financial keyword left after normalization and fell through to the
    # generic "summary" intent instead of a real answer.
    "මුළු ලැබීම්": "total receivable",
    "මුළු ආදායම": "total income",
    "ආදායම": "income",
    "මුළු වියදම": "total expenses",
    "වියදම": "expense",
    "මුළු ගෙවීම්": "total payable",
    "ශේෂය": "balance",
    "suppliersලාට ගෙවන්න": "owe supplier",
    "customersලා අපිට": "customers owe us",
    "ගෙවන්න තියෙන customerලා": "customers who haven't paid",
    "ගෙවලා නැති customerලා": "customers who haven't paid",
    "සල්ලි ගෙවලාද": "paid us",
    # Supplier/customer balance lookups phrased with a specific name — these used
    # to fall through to a document count. Word order disambiguates direction:
    #   "මට X ට කීයක් ගෙවන්න තියෙනවද"  = how much do I owe X  -> supplier
    #   "X මට කීයක් ගෙවන්න තියෙනවද"    = how much does X owe me -> customer
    # The longer "මට කීයක්…" phrase is matched first (longest-match), so only the
    # subject-first form ("X මට …") routes to the customer side.
    "මට කීයක් ගෙවන්න තියෙනවද": "customer owes me how much",
    "ට කීයක් ගෙවන්න තියෙනවද": "owe supplier how much",
    "ගෙන් කීයක් ලැබිය යුතුද": "customer owes me how much receivable",
    "ගෙන් කීයක් ලැබිය යුතු": "customer owes me how much receivable",
}


def normalize_query(question: str) -> str:
    """
    Iteration 10 (E): Pre-process mixed Sinhala/English queries so route_question
    can match English intent keywords. Replaces known Sinhala phrases with their
    English equivalents while preserving English and numeric tokens.
    """
    import unicodedata
    # Strip zero-width joiners and other invisible Unicode control chars
    cleaned = "".join(
        c for c in question
        if unicodedata.category(c) not in ("Cf",) or c in (" ", "\n", "\t")
    )
    # Apply longest-match substitution (sorted by length desc to prefer longer phrases)
    for si_phrase, en_phrase in sorted(_SINHALA_VERB_MAP.items(), key=lambda x: -len(x[0])):
        cleaned = cleaned.replace(si_phrase, en_phrase)
    return cleaned.strip()


def route_question(question: str):
    q = normalize_text(question)

    # ── Document lookup by ID (regex patterns: PO1234, INV-1045, DN-204, R5) ──
    # Exclude cross-document queries that happen to mention a doc ID
    _is_cross_doc = any(term in q for term in [
        "related to", "related po", "documents for", "linked to", "for order",
        "documents related", "related invoice", "po related", "show documents related",
        "delivery notes", "invoices for", "receipts for"
    ])
    if not _is_cross_doc and (
        re.search(r"\b(po|inv|dn|in|r)\s*[-#]?\s*\d+\b", q) or any(
            term in q for term in ["find po", "find invoice", "find dn", "find delivery", "find receipt",
                                     "show po", "status of po", "status of invoice",
                                     "හොයන්න", "open කරන්න", "document open"]
        )
    ):
        return "document_lookup"

    # ── Count queries ──
    if any(term in q for term in ["how many", "count", "number of", "කීයක්", "කීයද", "කීය"]):
        return "count_query"

    # ── Ambiguous overdue/pending (no doc type specified) ──
    if any(term in q for term in ["anything overdue", "anything pending", "still pending",
                                   "what is overdue", "any overdue", "any pending",
                                   "overdue documents", "pending documents",
                                   "what is outstanding", "documents overdue"]) and not any(
        doc in q for doc in ["invoice", "invoices", "po", "purchase order", "dn", "delivery"]
    ):
        return "ambiguous_status_query"

    # ── Monthly breakdown ──
    if any(term in q for term in ["by month", "revenue by", "sales by month", "monthly breakdown",
                                   "each month", "month by month", "monthly revenue",
                                   "monthly sales", "per month", "revenue per month",
                                   "monthly expenses", "monthly report"]):
        return "monthly_breakdown"

    # ── Financial comparison / profit ──
    if any(term in q for term in ["compare", "vs last month", "this month vs", "profit",
                                   "margin", "compare කරන්න", "vs", "this year vs",
                                   "last month vs", "month over month", "monthly trend"]):
        return "financial_comparison"

    # ── Supplier queries (before payable to avoid conflict) ──
    if any(term in q for term in ["supplier", "suppliers", "සැපයුම්කරු", "supply කරපු",
                                   "who supplied", "top supplier", "highest purchase",
                                   "supplier contact", "supplier transaction", "inactive supplier",
                                   "active supplier", "supplier balance", "supplier history",
                                   "වැඩියෙන්ම supply", "වැඩියෙන්ම purchases",
                                   "supplier has overdue", "suppliers from",
                                   "which supplier has", "supplier overdue"]):
        return "supplier_query"

    # ── Customer queries (before receivable to avoid conflict) ──
    if any(term in q for term in ["customer", "customers", "ගනුදෙනුකරු", "client", "clients",
                                   "top customer", "who owes", "customer balance", "customer history",
                                   "customer contact", "customer purchase", "new customer",
                                   "inactive customer", "active customer", "who bought",
                                   "who hasn't paid", "hasn't paid", "has not paid", "not paid yet",
                                   "who has not paid", "who haven't paid", "hasnt paid",
                                   "pay us", "paid us", "did they pay", "have they paid",
                                   "වැඩියෙන්ම ගත්ත", "ගෙවන්න තියෙන customerලා",
                                   "ගෙවන්නේ නැති", "customer register"]):
        return "customer_query"

    # ── Cross-document queries (before document_lookup to avoid ID match stealing it) ──
    _cross_terms = ["related to po", "documents for po", "linked to",
                    "for order", "for po", "related to invoice",
                    "documents related", "pto", "අදාල documents",
                    "po related", "show documents related",
                    "delivery notes show", "delivery notes for po",
                    "invoices for po", "receipts for po"]
    _has_doc_id = bool(re.search(r"\b(po|inv|dn|in|r)\s*[-#]?\s*\d+\b", q))
    if any(term in q for term in _cross_terms) or (
        _has_doc_id and any(t in q for t in ["delivery notes", "invoices for", "receipts for", "linked"])
    ):
        return "cross_document_query"

    # ── Activity / recent document queries ──
    if any(term in q for term in ["what happened", "created today", "created yesterday",
                                   "documents created", "activity", "recent documents",
                                   "what documents", "latest documents", "new documents",
                                   "හැදූ documents", "recent", "newly created"]):
        return "activity_query"

    # ── PO status queries (before generic po_list) ──
    _po_status_terms = [
        "open po", "open purchase order", "pending po", "approved po", "rejected po",
        "cancelled po", "fulfilled po", "overdue po", "partially delivered",
        "not delivered", "still open", "haven't been delivered", "reject",
        "still pending", "open තියෙන", "approve", "reject කරපු", "deliver නොවූ",
        "cancelled purchase", "open orders", "po status", "purchase order status",
        "who approved", "approved by", "approve කළේ",
        # standalone modifiers that combine with the doc-type check below
        "overdue", "pending", "approved", "rejected", "cancelled", "fulfilled",
        "partially", "not yet delivered", "not been delivered", "haven't delivered",
        "not delivered", "undelivered po",
    ]
    if any(term in q for term in _po_status_terms) and any(
        doc in q for doc in ["po", "purchase order", "purchase orders", "pos", "ඇණවුම"]
    ):
        return "po_status_query"

    # ── Invoice status queries (before generic invoice_list) ──
    _inv_status_terms = [
        "unpaid", "overdue invoice", "paid invoice", "partially paid", "cancelled invoice",
        "due today", "outstanding invoice", "due this week", "invoice due",
        "pay කරලා නැති", "overdue invoices", "paid invoices", "partially paid invoices",
        "invoice status", "which invoices are due", "due next", "due next week",
        "invoices need attention", "which invoices need", "attention",
        "been paid", "has been paid", "not done", "not paid", "have been paid",
        # standalone modifiers combined with the doc check below
        "overdue", "unpaid invoices", "fully paid", "cancelled", "pending",
    ]
    if any(term in q for term in _inv_status_terms) and any(
        doc in q for doc in ["invoice", "invoices", "ඉන්වොයිස්", "attention", "inv"]
    ):
        return "invoice_status_query"

    # ── DN status queries (before generic dn_list) ──
    _dn_status_terms = [
        "pending delivery", "delayed delivery", "delayed deliveries", "failed delivery",
        "returned delivery", "proof of delivery", "unsigned", "completed delivery",
        "delayed", "delay", "failed deliveries", "returned deliveries",
        "sign නොකරපු", "incomplete delivery", "undelivered",
        "delivery status", "delivery delayed", "are there any delayed",
        "completed deliveries", "which deliveries are", "which deliveries have",
        # standalone modifiers combined with the doc check below
        "pending deliveries", "failed", "returned", "pending", "completed",
    ]
    if any(term in q for term in _dn_status_terms) and any(
        doc in q for doc in ["dn", "delivery note", "delivery notes", "deliveries", "බෙදාහැරීම"]
    ):
        return "dn_status_query"

    # ── Payment queries ──
    if any(term in q for term in ["payment", "payments", "received payment", "pending payment",
                                   "who hasn't paid", "payment history", "total payments",
                                   "payments received", "payments this month",
                                   "ගෙවීම", "payments පෙන්නන්න", "pending payments",
                                   "fully paid", "partially paid invoice",
                                   "paid amount", "amount paid", "payment amount"]):
        return "payment_query"

    # ── Date-range queries (temporal modifier on any document type) ──
    _date_terms = ["today", "yesterday", "this week", "last week", "next week", "this month", "last month",
                   "this year", "last year", "between", "from january", "from february",
                   "from march", "from april", "from may", "from june", "from july",
                   "from august", "from september", "from october", "from november", "from december",
                   "january", "february", "march", "april", "june", "july", "august",
                   "september", "october", "november", "december",
                   "මේ මාසේ", "ගිය මාසේ", "අද", "ඊයේ", "මේ සතියේ", "ගිය සතියේ",
                   "මේ අවුරුද්දේ", "ගිය අවුරුද්දේ", "ජනවාරි", "පෙබරවාරි", "මාර්තු"]
    if any(term in q for term in _date_terms):
        return "date_range_query"

    # ── Receivable ──
    if any(term in q for term in ["receivable", "receive", "receiver", "received amount",
                                   "owe us", "customer owes", "they owe", "owes me",
                                   "අයකරගත", "ලැබිය යුතු", "සල්ලි ගෙවලාද",
                                   "ගෙවන්න තියෙන", "customersලා අපිට"]):
        return "receivable"

    # ── Payable ──
    if any(term in q for term in ["payable", "amount due", "we owe", "owe supplier",
                                   "ගෙවිය යුතු", "suppliersලාට ගෙවන්න", "how much do we owe",
                                   "outstanding balance", "outstanding amount", "outstanding",
                                   "balance", "remaining balance", "total outstanding"]):
        return "payable"

    # ── Invoice list ──
    if any(term in q for term in ["invoice", "invoices", "show invoices", "ඉන්වොයිස්",
                                   "all invoices", "invoice history", "invoice list"]):
        return "invoice_list"

    # ── Receipt list ──
    if any(term in q for term in ["receipt", "receipts", "show receipts", "රිසිට්"]):
        return "receipt_list"

    # ── PO list ──
    if any(term in q for term in ["po", "purchase order", "ඇණවුම", "all purchase orders",
                                   "my purchase orders", "purchase orders", "latest po",
                                   "new po", "newest po"]):
        return "po_list"

    # ── DN list ──
    if any(term in q for term in ["dn", "delivery note", "delivery notes", "deliveries",
                                   "බෙදාහැරීම", "all deliveries", "delivery history",
                                   "show deliveries"]):
        return "dn_list"

    # ── Cash inflow / outflow ──
    if any(term in q for term in ["cash inflow", "cash_inflow", "inflow"]):
        return "cash_inflow"

    if any(term in q for term in ["cash outflow", "cash_outflow", "outflow"]):
        return "cash_outflow"

    if any(term in q for term in ["expense", "expenses", "expence", "spent", "cost", "expenditure"]):
        return "expenses"

    if any(term in q for term in ["income", "revenue", "earned", "earning", "revenues",
                                   "sales", "total sales"]):
        return "revenue"

    return "summary"


def normalize_flow(value):
    if not value:
        return "unknown"

    v = str(value).strip().lower().replace(" ", "_")

    if v in ("receivable", "receive"):
        return "receivable"
    if v in ("payable", "pay"):
        return "payable"
    if v in ("cash_inflow", "income", "inflow"):
        return "cash_inflow"
    if v in ("cash_outflow", "expense", "expence", "outflow"):
        return "cash_outflow"

    return "unknown"


def build_evidence(records: pd.DataFrame, reason: str):
    evidence = []

    for _, row in records.iterrows():
        # Use final_total_amount as the authoritative amount — it is flow-neutral and
        # reflects the confirmed document total after normalization and arithmetic correction.
        # payable_amount is semantically wrong as a primary source for receivable documents.
        amount_used = to_float(row.get("final_total_amount", 0))
        if amount_used == 0.0:
            amount_used = to_float(row.get("payable_amount", 0))
        if amount_used == 0.0:
            amount_used = to_float(row.get("raw_total_amount", 0))

        raw_flow = str(row.get("flow_type", "unknown"))
        flow_direction = (
            "income" if normalize_flow(raw_flow) in ("receivable", "cash_inflow")
            else "expense"
        )

        evidence.append({
            "document_id": row.get("document_id", "NULL"),
            "document_type": row.get("document_type", "NULL"),
            "date": row.get("date", "NULL"),
            "company_name": row.get("company_name", "NULL"),
            "supplier_name": row.get("supplier_name", "NULL"),
            "order_id": row.get("order_id", "NULL"),
            "flow_type": raw_flow,
            "flow_direction": flow_direction,
            "received_status": row.get("received_status", "NULL"),
            "paid_status": row.get("paid_status", "NULL"),
            # Iteration 10: workflow status fields
            "po_status": row.get("po_status", "NULL") if str(row.get("po_status", "NULL")) not in ("NULL", "None", "") else None,
            "dn_status": row.get("dn_status", "NULL") if str(row.get("dn_status", "NULL")) not in ("NULL", "None", "") else None,
            "invoice_status": row.get("invoice_status", "NULL") if str(row.get("invoice_status", "NULL")) not in ("NULL", "None", "") else None,
            "due_date": row.get("due_date", None) if str(row.get("due_date", "NULL")) not in ("NULL", "None", "") else None,
            "delivery_date": row.get("delivery_date", None) if str(row.get("delivery_date", "NULL")) not in ("NULL", "None", "") else None,
            "approved_by": row.get("approved_by", None) if str(row.get("approved_by", "NULL")) not in ("NULL", "None", "") else None,
            "proof_of_delivery": row.get("proof_of_delivery", None),
            "signed": row.get("signed", None),
            "currency": row.get("currency", "NULL"),
            "final_total_amount": float(row.get("final_total_amount", 0) or 0),
            "payable_amount": float(row.get("payable_amount", 0) or 0),
            "tax_amount": to_float(row.get("tax_amount")),
            "tax_rate": to_float(row.get("tax_rate")),
            "discount_amount": to_float(row.get("discount_amount")),
            "amount_used": amount_used,
            "items": extract_items_from_row(row),
            "reason_used": reason,
        })

    return evidence


def extract_named_entity(question: str):
    q = normalize_text(question)

    patterns = [
        # "how much do we owe TechRent LK" / "owe to TechRent" — party follows "owe"
        r"\bowe\s+(?:to\s+)?([a-zA-Z0-9 .,&'-]+)",
        r"\bpay\s+(?:to\s+)?([a-zA-Z0-9 .,&'-]+)",
        r"\bfrom\s+([a-zA-Z0-9 .,&'-]+)",
        r"\bby\s+([a-zA-Z0-9 .,&'-]+)",
        r"\bfor\s+([a-zA-Z0-9 .,&'-]+)",
        r"\bof\s+([a-zA-Z0-9 .,&'-]+)",
    ]

    stop_words = {
        "company", "invoice", "invoices", "receipt", "receipts", "po", "dn",
        "payable", "receivable", "amount", "total", "documents", "document",
        "we", "have", "what", "is", "the", "a", "an",
        # verbs/fillers that can precede the captured party token
        "how", "much", "do", "does", "owe", "owes", "owed", "pay", "to",
        "us", "me", "our", "still", "all", "much",
    }

    for pattern in patterns:
        match = re.search(pattern, q)
        if match:
            candidate = normalize_text(match.group(1))
            candidate_words = [w for w in candidate.split() if w not in stop_words]
            candidate = " ".join(candidate_words).strip()
            if candidate:
                return candidate

    return ""


def filter_named_entity(records: pd.DataFrame, entity_name: str):
    target = normalize_text(entity_name)
    if not target:
        return records.copy()

    # Match space/punctuation-insensitively too, so "techrentlk" matches the
    # stored supplier "TechRent LK" (and vice-versa).
    target_compact = re.sub(r"[^a-z0-9]", "", target)

    def _match(col: pd.Series) -> pd.Series:
        norm = col.apply(normalize_text)
        m = norm.str.contains(re.escape(target), na=False)
        if target_compact:
            compact = norm.str.replace(r"[^a-z0-9]", "", regex=True)
            m = m | compact.str.contains(target_compact, na=False)
        return m

    company_match = _match(records["company_name"])
    supplier_match = _match(records["supplier_name"])

    filtered = records[company_match | supplier_match].copy()
    return filtered


def sum_amounts(records: pd.DataFrame):
    total_amount = 0.0

    for _, row in records.iterrows():
        amount = preferred_amount(row)
        total_amount += amount

    return round(total_amount, 2)

def wants_items(question: str):
    q = normalize_text(question)
    return any(word in q for word in [
        "item", "items", "product", "products", "description", "breakdown",
        "separately", "separate", "each", "details", "detail",
        "අයිතම", "වෙන වෙනම", "විස්තර"
    ])


def build_direct_answer(records: pd.DataFrame, question_type: str, company_name: str, question: str):
    if records.empty:
        return "No matching records found."

    show_items = wants_items(question)
    total = sum_amounts(records)

    lines = []

    for _, row in records.iterrows():
        doc_id = row.get("document_id", "NULL")
        supplier = row.get("supplier_name", "NULL")
        doc_type = row.get("document_type", "NULL")
        flow = row.get("flow_type", "NULL")
        currency = row.get("currency", "LKR")
        amount = preferred_amount(row)

        if str(currency).strip().upper() in ["", "NULL", "NONE"]:
            currency = "LKR"

        lines.append(f"{doc_id} - {supplier} ({doc_type}, {flow}): {currency} {amount}")

        if show_items:
            items = extract_items_from_row(row)

            if items:
                for item in items:
                    desc = item.get("description", "NULL")
                    qty = item.get("quantity", "NULL")
                    unit_price = item.get("unit_price", "NULL")
                    line_total = item.get("line_total", "NULL")
                    lines.append(
                        f"  - {desc} | Qty: {qty} | Unit Price: {unit_price} | Line Total: {line_total}"
                    )
            else:
                lines.append("  - No item details found for this document.")

    labels = {
        "receivable":  "Total Receivable",
        "payable":     "Total Payable",
        "revenue":     "Total Revenue",
        "expenses":    "Total Expenses",
        "cash_inflow": "Total Cash Inflow",
        "cash_outflow":"Total Cash Outflow",
        # legacy aliases
        "income":  "Total Income",
        "expense": "Total Expense",
    }
    label = labels.get(question_type, "Total")
    lines.append(f"{label}: LKR {total}")

    return "\n".join(lines)

# ─────────────────── Iteration 10: new query helpers ────────────────────────


def parse_amount_filter(question: str) -> tuple[float | None, float | None]:
    """
    Extract an amount range filter from natural language.
    Returns (min_amount, max_amount) — either may be None.
    Handles: "above 500000", "more than Rs.100,000", "below 50000",
             "between 10000 and 50000", "under Rs.200,000", "over 1 million"
    """
    q = question.lower().replace(",", "").replace("rs.", "").replace("rs", "").replace("lkr", "").strip()

    def _parse_num(s: str) -> float | None:
        s = s.strip().replace(",", "")
        try:
            # "1 million" / "1m"
            if "million" in s or s.endswith("m"):
                n = re.sub(r"[^\d.]", "", s)
                return float(n) * 1_000_000 if n else None
            # "1 lakh" / "100k"
            if "lakh" in s or s.endswith("k"):
                n = re.sub(r"[^\d.]", "", s)
                return float(n) * 100_000 if n else None
            n = re.sub(r"[^\d.]", "", s)
            return float(n) if n else None
        except Exception:
            return None

    # "between X and Y"
    bet = re.search(r"between\s+([\d.,\s]+(million|lakh|[km])?)\s+and\s+([\d.,\s]+(million|lakh|[km])?)", q)
    if bet:
        lo = _parse_num(bet.group(1))
        hi = _parse_num(bet.group(3))
        if lo is not None and hi is not None:
            return lo, hi

    # "above / more than / over / greater than X"
    above = re.search(r"(?:above|more than|over|greater than|exceeding)\s+([\d.,\s]+(million|lakh|[km])?)", q)
    if above:
        val = _parse_num(above.group(1))
        if val is not None:
            return val, None

    # "below / less than / under / within X"
    below = re.search(r"(?:below|less than|under|within)\s+([\d.,\s]+(million|lakh|[km])?)", q)
    if below:
        val = _parse_num(below.group(1))
        if val is not None:
            return None, val

    return None, None


def apply_amount_filter(df: pd.DataFrame, min_amount: float | None, max_amount: float | None) -> pd.DataFrame:
    """Filter DataFrame rows to those whose preferred amount falls within the range."""
    if min_amount is None and max_amount is None:
        return df

    def _in_range(row) -> bool:
        amt = preferred_amount(row)
        if min_amount is not None and amt < min_amount:
            return False
        if max_amount is not None and amt > max_amount:
            return False
        return True

    return df[df.apply(_in_range, axis=1)].copy()


def _extract_contact_from_row(row) -> dict:
    """
    Return contact info for a document row.
    Priority: stored DB fields (Iteration 11) → regex from OCR text (fallback).
    """
    def _val(field):
        v = row.get(field, "") or ""
        return str(v).strip() if str(v).strip().upper() not in ("NULL", "NONE", "") else ""

    phone = _val("supplier_phone")
    email = _val("supplier_email")
    city  = _val("supplier_city")

    # Regex fallback from OCR text when DB fields are empty
    if not phone or not email:
        text = str(row.get("corrected_text", "") or row.get("raw_text", ""))
        if text.strip().upper() not in ("NULL", "NONE", ""):
            if not phone:
                phones = re.findall(r"(?:\+94|0)[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{4}", text)
                phone = phones[0].strip() if phones else ""
            if not email:
                emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
                email = emails[0].strip() if emails else ""

    return {"phone": phone, "email": email, "city": city}


_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "ජනවාරි": 1, "පෙබරවාරි": 2, "මාර්තු": 3,
}


def parse_temporal_filter(question: str):
    """
    Extract a (date_from, date_to) range from natural language.
    Returns (None, None) when no temporal expression is found.
    """
    q = normalize_text(question)
    today = date.today()

    if "yesterday" in q or "ඊයේ" in q:
        d = today - timedelta(days=1)
        return d, d

    if "today" in q or "අද" in q:
        return today, today

    if any(t in q for t in ["this week", "මේ සතියේ"]):
        start = today - timedelta(days=today.weekday())
        return start, today

    if any(t in q for t in ["last week", "ගිය සතියේ"]):
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=6)
        return start, end

    if any(t in q for t in ["this month", "මේ මාසේ"]):
        return today.replace(day=1), today

    if any(t in q for t in ["last month", "ගිය මාසේ"]):
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end

    if any(t in q for t in ["this year", "මේ අවුරුද්දේ"]):
        return today.replace(month=1, day=1), today

    if any(t in q for t in ["last year", "ගිය අවුරුද්දේ"]):
        y = today.year - 1
        return date(y, 1, 1), date(y, 12, 31)

    # "between January and March" / "from January to March"
    between = re.search(r"(from|between)\s+(\w+)\s+(to|and)\s+(\w+)", q)
    if between:
        m1 = _MONTH_NAMES.get(between.group(2))
        m2 = _MONTH_NAMES.get(between.group(4))
        if m1 and m2:
            yr = today.year
            last_day = calendar.monthrange(yr, m2)[1]
            return date(yr, m1, 1), date(yr, m2, last_day)

    # Single month name: "january invoices"
    for month_name, month_num in _MONTH_NAMES.items():
        if month_name in q:
            yr = today.year
            last_day = calendar.monthrange(yr, month_num)[1]
            return date(yr, month_num, 1), date(yr, month_num, last_day)

    return None, None


def _parse_date_for_filter(date_str) -> date | None:
    """Parse various date string formats to a date object."""
    if not date_str or str(date_str).strip().upper() in ("", "NULL", "NONE"):
        return None
    s = str(date_str).strip()
    # Try ISO format first (YYYY-MM-DD) — dateutil with dayfirst=True misparses these.
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    try:
        from dateutil import parser as _dp
        return _dp.parse(s, dayfirst=True).date()
    except Exception:
        return None


def apply_date_filter(df: pd.DataFrame, date_from: date | None, date_to: date | None) -> pd.DataFrame:
    """Filter DataFrame rows to those whose date falls within [date_from, date_to]."""
    if date_from is None and date_to is None:
        return df

    def _in_range(row_date_str) -> bool:
        d = _parse_date_for_filter(row_date_str)
        if d is None:
            return False
        if date_from and d < date_from:
            return False
        if date_to and d > date_to:
            return False
        return True

    date_col = "date" if "date" in df.columns else None
    if not date_col:
        return df

    mask = df[date_col].apply(_in_range)
    return df[mask].copy()


def _extract_doc_id_from_question(question: str) -> str | None:
    """Extract a document ID pattern like PO1045, INV-1045, DN-204 from the question."""
    q = question.upper()
    patterns = [
        r"\b(PO|IN|INV|DN|R)\s*[-#]?\s*(\d+)\b",
        r"\b(PO|PURCHASE\s*ORDER)\s*[-#]?\s*(\d+)\b",
    ]
    for pat in patterns:
        m = re.search(pat, q)
        if m:
            prefix = m.group(1).replace(" ", "").replace("PURCHASE ORDER", "PO").replace("INV", "IN")
            num = m.group(2)
            return f"{prefix}{num}"
    # Also match bare numbers when preceded by context
    m = re.search(r"\b(\d{3,6})\b", question)
    if m:
        return m.group(1)
    return None


def handle_document_lookup(question: str, df: pd.DataFrame, company_name: str):
    doc_id = _extract_doc_id_from_question(question)
    if not doc_id:
        return None

    # Try exact then partial match
    mask = df["document_id"].apply(normalize_text).str.contains(normalize_text(doc_id), na=False)
    result_df = df[mask].copy()
    if result_df.empty:
        return None

    row = result_df.iloc[0]
    doc_type = row.get("document_type", "document")
    currency = str(row.get("currency", "LKR")).strip() or "LKR"
    amount = preferred_amount(row)

    status_parts = []
    if row.get("po_status") and str(row.get("po_status", "")).upper() not in ("NULL", "NONE", ""):
        status_parts.append(f"PO Status: {row['po_status'].title()}")
    if row.get("dn_status") and str(row.get("dn_status", "")).upper() not in ("NULL", "NONE", ""):
        status_parts.append(f"Delivery Status: {row['dn_status'].title()}")
    if row.get("invoice_status") and str(row.get("invoice_status", "")).upper() not in ("NULL", "NONE", ""):
        status_parts.append(f"Invoice Status: {row['invoice_status'].title()}")
    if row.get("approved_by") and str(row.get("approved_by", "")).upper() not in ("NULL", "NONE", ""):
        status_parts.append(f"Approved By: {row['approved_by']}")
    if row.get("due_date") and str(row.get("due_date", "")).upper() not in ("NULL", "NONE", ""):
        status_parts.append(f"Due Date: {row['due_date']}")
    if row.get("delivery_date") and str(row.get("delivery_date", "")).upper() not in ("NULL", "NONE", ""):
        status_parts.append(f"Delivery Date: {row['delivery_date']}")

    paid_s = row.get("paid_status", "")
    recv_s = row.get("received_status", "")
    if paid_s and str(paid_s).upper() not in ("NULL", "NONE", ""):
        status_parts.append(f"Payment: {paid_s.replace('_', ' ').title()}")
    if recv_s and str(recv_s).upper() not in ("NULL", "NONE", ""):
        status_parts.append(f"Received: {recv_s.replace('_', ' ').title()}")

    status_str = " | ".join(status_parts) if status_parts else "No status info"
    direct_answer = (
        f"Document {doc_id} ({doc_type.upper()})\n"
        f"Party: {row.get('supplier_name', 'Unknown')}\n"
        f"Date: {row.get('date', 'Unknown')}\n"
        f"Amount: {currency} {amount:,.2f}\n"
        f"{status_str}"
    )

    return {
        "success": True,
        "question_type": "document_lookup",
        "explanation": f"Found document '{doc_id}' in your records.",
        "evidence": build_evidence(result_df, f"Document lookup by ID: {doc_id}"),
        "metrics": {"document_id": doc_id, "document_type": doc_type, "amount": amount},
        "source_file": DATASET_PATH,
        "direct_answer": direct_answer,
    }


def handle_po_status_query(question: str, df: pd.DataFrame, company_name: str):
    q = normalize_text(question)
    po_df = df[df["document_type"].apply(normalize_text) == "po"].copy()

    # Determine which statuses to filter by
    today = date.today()

    if any(t in q for t in ["approve", "approved"]):
        status_filter = ["approved"]
        label = "Approved"
    elif any(t in q for t in ["reject", "rejected"]):
        status_filter = ["rejected"]
        label = "Rejected"
    elif any(t in q for t in ["cancel", "cancelled"]):
        status_filter = ["cancelled"]
        label = "Cancelled"
    elif any(t in q for t in ["fulfilled", "completed", "delivered"]) and not any(
        t in q for t in ["partially", "partial"]
    ):
        status_filter = ["fulfilled"]
        label = "Fulfilled"
    elif any(t in q for t in ["partially", "partial", "partially delivered"]):
        status_filter = ["partially_delivered"]
        label = "Partially Delivered"
    elif any(t in q for t in ["overdue"]):
        # Pending POs where delivery_date has passed
        def _is_overdue(row):
            if str(row.get("po_status", "")).lower() in ("fulfilled", "cancelled"):
                return False
            dd = _parse_date_for_filter(row.get("delivery_date", ""))
            return dd is not None and dd < today

        result_df = po_df[po_df.apply(_is_overdue, axis=1)].copy()
        total = sum_amounts(result_df)
        return {
            "success": True,
            "question_type": "po_status_query",
            "explanation": f"Overdue purchase orders (delivery date passed, not yet fulfilled) for '{company_name}'.",
            "evidence": build_evidence(result_df, "Overdue PO: delivery date has passed."),
            "metrics": {"po_count": len(result_df), "total_amount": total, "status_filter": "overdue"},
            "source_file": DATASET_PATH,
            "direct_answer": _format_po_list(result_df, "Overdue", total),
        }
    else:
        # Default: open/pending = pending + approved
        status_filter = ["pending", "approved"]
        label = "Open"

    result_df = po_df[
        po_df["po_status"].apply(lambda x: str(x).lower() if x else "pending").isin(status_filter)
    ].copy()

    # Apply amount range filter if specified
    min_amt, max_amt = parse_amount_filter(question)
    result_df = apply_amount_filter(result_df, min_amt, max_amt)
    if min_amt is not None or max_amt is not None:
        range_note = f" (amount filter: {'> LKR {:,.0f}'.format(min_amt) if min_amt else ''}{' – ' if min_amt and max_amt else ''}{'< LKR {:,.0f}'.format(max_amt) if max_amt else ''})"
        label = label + range_note

    total = sum_amounts(result_df)

    return {
        "success": True,
        "question_type": "po_status_query",
        "explanation": f"{label} purchase orders for '{company_name}'.",
        "evidence": build_evidence(result_df, f"PO status filter: {label}."),
        "metrics": {"po_count": len(result_df), "total_amount": total, "status_filter": label.lower()},
        "source_file": DATASET_PATH,
        "direct_answer": _format_po_list(result_df, label, total),
    }


def _format_po_list(df: pd.DataFrame, label: str, total: float) -> str:
    if df.empty:
        return f"No {label.lower()} purchase orders found."
    lines = [f"{label} Purchase Orders ({len(df)} found):"]
    for _, row in df.iterrows():
        currency = str(row.get("currency", "LKR")).strip() or "LKR"
        amount = preferred_amount(row)
        status = str(row.get("po_status", "pending")).replace("_", " ").title()
        supplier = row.get("supplier_name", "Unknown")
        lines.append(
            f"  {row.get('document_id','?')} | {supplier} | {row.get('date','?')} "
            f"| {currency} {amount:,.2f} | {status}"
        )
    if total > 0:
        lines.append(f"Total: LKR {total:,.2f}")
    return "\n".join(lines)


def handle_invoice_status_query(question: str, df: pd.DataFrame, company_name: str):
    q = normalize_text(question)
    inv_df = df[df["document_type"].apply(normalize_text) == "invoice"].copy()
    today = date.today()

    if any(t in q for t in ["unpaid", "outstanding", "not paid", "pay කරලා නැති",
                              "who hasn't paid", "overdue", "still owe"]):
        result_df = inv_df[
            inv_df["invoice_status"].apply(
                lambda x: str(x).lower() in ("pending", "overdue", "partially_paid", "")
                if x and str(x).upper() not in ("NULL", "NONE") else True
            )
        ].copy()
        label = "Unpaid"
    elif any(t in q for t in ["overdue"]):
        result_df = inv_df[
            inv_df["invoice_status"].apply(lambda x: str(x).lower() == "overdue").fillna(False)
        ].copy()
        if result_df.empty:
            # Fallback: paid_status = not_paid and due_date passed
            def _is_overdue(row):
                if str(row.get("paid_status", "")).lower() == "paid":
                    return False
                dd = _parse_date_for_filter(row.get("due_date", ""))
                return dd is not None and dd < today
            result_df = inv_df[inv_df.apply(_is_overdue, axis=1)].copy()
        label = "Overdue"
    elif any(t in q for t in ["paid invoice", "paid invoices", "fully paid", "paid invoices"]):
        result_df = inv_df[
            inv_df["invoice_status"].apply(lambda x: str(x).lower() == "paid").fillna(False)
            | inv_df["paid_status"].apply(normalize_text).isin(["paid"])
        ].copy()
        label = "Paid"
    elif any(t in q for t in ["partially paid", "partial payment"]):
        result_df = inv_df[
            inv_df["invoice_status"].apply(lambda x: str(x).lower() == "partially_paid").fillna(False)
            | inv_df["paid_status"].apply(normalize_text).isin(["partial"])
        ].copy()
        label = "Partially Paid"
    elif any(t in q for t in ["cancel", "cancelled"]):
        result_df = inv_df[
            inv_df["invoice_status"].apply(lambda x: str(x).lower() == "cancelled").fillna(False)
        ].copy()
        label = "Cancelled"
    elif any(t in q for t in ["due today"]):
        result_df = inv_df[
            inv_df["due_date"].apply(lambda x: _parse_date_for_filter(x) == today)
        ].copy()
        label = "Due Today"
    else:
        result_df = inv_df.copy()
        label = "All"

    total = sum_amounts(result_df)
    outstanding = sum_amounts(result_df[
        result_df.get("paid_status", pd.Series(dtype=str)).apply(normalize_text) != "paid"
    ]) if not result_df.empty else 0.0

    lines = [f"{label} Invoices ({len(result_df)} found):"]
    for _, row in result_df.iterrows():
        currency = str(row.get("currency", "LKR")).strip() or "LKR"
        amount = preferred_amount(row)
        status = str(row.get("invoice_status", row.get("paid_status", ""))).replace("_", " ").title()
        lines.append(
            f"  {row.get('document_id','?')} | {row.get('supplier_name','?')} "
            f"| {row.get('date','?')} | {currency} {amount:,.2f} | {status}"
        )
    if total > 0:
        lines.append(f"Total: LKR {total:,.2f}")

    return {
        "success": True,
        "question_type": "invoice_status_query",
        "explanation": f"{label} invoices for '{company_name}'.",
        "evidence": build_evidence(result_df, f"Invoice status filter: {label}."),
        "metrics": {
            "invoice_count": len(result_df),
            "total_amount": total,
            "outstanding_amount": outstanding,
            "status_filter": label.lower(),
        },
        "source_file": DATASET_PATH,
        "direct_answer": "\n".join(lines) if result_df.empty is False else f"No {label.lower()} invoices found.",
    }


def handle_dn_status_query(question: str, df: pd.DataFrame, company_name: str):
    q = normalize_text(question)
    dn_df = df[df["document_type"].apply(normalize_text) == "dn"].copy()
    today = date.today()

    if any(t in q for t in ["pending delivery", "pending deliveries", "not delivered", "pending"]):
        result_df = dn_df[
            dn_df["dn_status"].apply(lambda x: str(x).lower() in ("pending", "") if x and str(x).upper() not in ("NULL", "NONE") else True)
        ].copy()
        label = "Pending"
    elif any(t in q for t in ["delayed", "delay", "late delivery"]):
        result_df = dn_df[
            dn_df["dn_status"].apply(lambda x: str(x).lower() == "delayed").fillna(False)
        ].copy()
        if result_df.empty:
            def _is_delayed(row):
                if str(row.get("dn_status", "")).lower() in ("delivered", "cancelled"):
                    return False
                dd = _parse_date_for_filter(row.get("delivery_date", ""))
                return dd is not None and dd < today
            result_df = dn_df[dn_df.apply(_is_delayed, axis=1)].copy()
        label = "Delayed"
    elif any(t in q for t in ["completed delivery", "delivered", "complete deliveries"]):
        result_df = dn_df[
            dn_df["dn_status"].apply(lambda x: str(x).lower() == "delivered").fillna(False)
            | dn_df["received_status"].apply(normalize_text).isin(["received"])
        ].copy()
        label = "Delivered"
    elif any(t in q for t in ["failed delivery", "failed", "fail"]):
        result_df = dn_df[
            dn_df["dn_status"].apply(lambda x: str(x).lower() == "failed").fillna(False)
        ].copy()
        label = "Failed"
    elif any(t in q for t in ["returned", "return"]):
        result_df = dn_df[
            dn_df["dn_status"].apply(lambda x: str(x).lower() == "returned").fillna(False)
        ].copy()
        label = "Returned"
    elif any(t in q for t in ["proof of delivery", "pod"]):
        result_df = dn_df[dn_df["proof_of_delivery"].apply(lambda x: x is True)].copy()
        label = "With Proof of Delivery"
    elif any(t in q for t in ["unsigned", "sign නොකරපු", "not signed"]):
        result_df = dn_df[
            dn_df["signed"].apply(lambda x: x is False or x is None)
        ].copy()
        label = "Unsigned"
    else:
        result_df = dn_df.copy()
        label = "All"

    lines = [f"{label} Delivery Notes ({len(result_df)} found):"]
    for _, row in result_df.iterrows():
        status = str(row.get("dn_status", row.get("received_status", ""))).replace("_", " ").title()
        lines.append(
            f"  {row.get('document_id','?')} | {row.get('supplier_name','?')} "
            f"| {row.get('date','?')} | {status}"
        )

    return {
        "success": True,
        "question_type": "dn_status_query",
        "explanation": f"{label} delivery notes for '{company_name}'.",
        "evidence": build_evidence(result_df, f"DN status filter: {label}."),
        "metrics": {"dn_count": len(result_df), "status_filter": label.lower()},
        "source_file": DATASET_PATH,
        "direct_answer": "\n".join(lines) if not result_df.empty else f"No {label.lower()} delivery notes found.",
    }


def handle_date_range_query(question: str, df: pd.DataFrame, company_name: str):
    q = normalize_text(question)
    date_from, date_to = parse_temporal_filter(question)
    result_df = apply_date_filter(df, date_from, date_to)

    # Detect income/expense context so we filter by flow direction, not just date.
    # Without this, "how much income did we earn last month?" returns ALL documents
    # in the period (payables mixed with receivables).
    # Use word stems as substrings: "earn" also catches earned/earning/earnings.
    _income_terms = ["income", "revenue", "earn", "sale", "turnover",
                     "ආදාය", "ලැබිය", "ඉපැය", "receivable", "inflow", "cash inflow"]
    _expense_terms = ["expense", "expence", "spent", "spend", "spending", "cost", "expenditure",
                      "වියද", "ගෙවිය", "payable", "outflow", "cash outflow"]
    wants_income = any(t in q for t in _income_terms)
    wants_expense = any(t in q for t in _expense_terms)

    if wants_income and not wants_expense:
        result_df = result_df[
            result_df["flow_type"].apply(normalize_flow).isin(["receivable", "cash_inflow"])
        ].copy()
        flow_label = "Income"
        question_type_out = "revenue"
    elif wants_expense and not wants_income:
        result_df = result_df[
            result_df["flow_type"].apply(normalize_flow).isin(["payable", "cash_outflow"])
        ].copy()
        flow_label = "Expenses"
        question_type_out = "expenses"
    else:
        flow_label = ""
        question_type_out = "date_range_query"

    # Apply doc type filter if mentioned
    if any(t in q for t in ["invoice", "invoices", "ඉන්වොයිස්"]):
        result_df = result_df[result_df["document_type"].apply(normalize_text) == "invoice"]
        doc_label = f"{flow_label} Invoices" if flow_label else "Invoices"
    elif any(t in q for t in ["po", "purchase order", "ඇණවුම"]):
        result_df = result_df[result_df["document_type"].apply(normalize_text) == "po"]
        doc_label = "Purchase Orders"
    elif any(t in q for t in ["dn", "delivery note", "delivery", "deliveries", "බෙදාහැරීම"]):
        result_df = result_df[result_df["document_type"].apply(normalize_text) == "dn"]
        doc_label = "Delivery Notes"
    elif any(t in q for t in ["receipt", "receipts", "රිසිට්"]):
        result_df = result_df[result_df["document_type"].apply(normalize_text) == "receipt"]
        doc_label = f"{flow_label} Receipts" if flow_label else "Receipts"
    else:
        doc_label = flow_label if flow_label else "Documents"

    period_label = ""
    if date_from and date_to:
        if date_from == date_to:
            period_label = str(date_from)
        else:
            period_label = f"{date_from} to {date_to}"

    total = sum_amounts(result_df)
    lines = [f"{doc_label} for {period_label} ({len(result_df)} found):"]
    for _, row in result_df.iterrows():
        currency = str(row.get("currency", "LKR")).strip() or "LKR"
        amount = preferred_amount(row)
        lines.append(
            f"  {row.get('document_id','?')} | {row.get('supplier_name','?')} "
            f"| {row.get('date','?')} | {currency} {amount:,.2f}"
        )
    if total > 0:
        lines.append(f"Total: LKR {total:,.2f}")

    explanation_prefix = f"Total {flow_label.lower()} " if flow_label else ""
    return {
        "success": True,
        "question_type": question_type_out,
        "explanation": f"{explanation_prefix}{doc_label} from {period_label} for '{company_name}'.",
        "evidence": build_evidence(result_df, f"Date range filter: {period_label}."),
        "metrics": {
            "document_count": len(result_df),
            "total_amount": total,
            "period": period_label,
            "doc_type": doc_label,
        },
        "source_file": DATASET_PATH,
        "direct_answer": "\n".join(lines) if not result_df.empty else f"No {doc_label.lower()} found for {period_label}.",
    }


def handle_supplier_query(question: str, df: pd.DataFrame, company_name: str):
    q = normalize_text(question)

    # Only payable/PO documents represent supplier relationships
    supplier_df = df[
        df["flow_type"].apply(normalize_flow).isin(["payable", "cash_outflow"])
        | df["document_type"].apply(normalize_text).isin(["po"])
    ].copy()

    if supplier_df.empty:
        return {
            "success": True,
            "question_type": "supplier_query",
            "explanation": f"No supplier transactions found for '{company_name}'.",
            "evidence": [],
            "metrics": {"supplier_count": 0},
            "source_file": DATASET_PATH,
            "direct_answer": "No supplier records found.",
        }

    # Entity filter: "ABC Traders"
    entity = extract_named_entity(question)
    if entity:
        candidate = filter_named_entity(supplier_df, entity)
        if not candidate.empty:
            supplier_df = candidate

    # ── City filter: "suppliers from Colombo" ──────────────────────────────────
    city_filter = ""
    city_pattern = re.search(r"from\s+([A-Za-z][a-zA-Z\s]{2,20})", q)
    if city_pattern:
        city_filter = normalize_text(city_pattern.group(1))

    if city_filter:
        def _city_matches(row) -> bool:
            city = normalize_text(row.get("supplier_city", "") or "")
            text = normalize_text(str(row.get("corrected_text", "") or row.get("raw_text", "")))
            return city_filter in city or city_filter in text

        candidate = supplier_df[supplier_df.apply(_city_matches, axis=1)].copy()
        if not candidate.empty:
            supplier_df = candidate

    # ── Overdue-invoice cross-reference: "which supplier has overdue invoices?" ─
    wants_overdue_inv = any(t in q for t in ["overdue invoice", "overdue invoices",
                                              "has overdue", "have overdue", "with overdue"])
    if wants_overdue_inv:
        # Find supplier names that appear in overdue invoices
        all_docs = df  # full tenant data passed as df param
        overdue_inv_df = all_docs[
            (all_docs["document_type"].apply(normalize_text) == "invoice")
            & (all_docs["invoice_status"].apply(
                lambda x: str(x).lower() == "overdue"
                if x and str(x).upper() not in ("NULL", "NONE") else False
            ))
        ]
        overdue_suppliers = set(
            normalize_text(str(r.get("supplier_name", "")))
            for _, r in overdue_inv_df.iterrows()
            if r.get("supplier_name")
        )
        if overdue_suppliers:
            supplier_df = supplier_df[
                supplier_df["supplier_name"].apply(normalize_text).isin(overdue_suppliers)
            ].copy()

    # Aggregate by supplier — collect total, count, last_date, and contact info
    _cutoff_date = (date.today() - timedelta(days=90)).strftime("%Y-%m-%d")
    groups = {}
    for _, row in supplier_df.iterrows():
        name = normalize_text(row.get("supplier_name", "Unknown")) or "Unknown"
        if name not in groups:
            groups[name] = {"total": 0.0, "count": 0, "last_date": "", "phone": "", "email": "", "city": ""}
        groups[name]["total"] += preferred_amount(row)
        groups[name]["count"] += 1
        d = str(row.get("date", "")) or ""
        if d > groups[name]["last_date"]:
            groups[name]["last_date"] = d
        # Use DB fields first, then regex fallback
        if not groups[name]["phone"] and not groups[name]["email"]:
            contact = _extract_contact_from_row(row)
            groups[name]["phone"] = contact.get("phone", "")
            groups[name]["email"] = contact.get("email", "")
            if not groups[name]["city"] and contact.get("city"):
                groups[name]["city"] = contact["city"]

    # Active = last transaction within 90 days
    for name, stats in groups.items():
        stats["active"] = stats["last_date"] >= _cutoff_date if stats["last_date"] else False

    # Detect query sub-type
    wants_top = any(t in q for t in ["top", "highest", "most", "biggest", "greatest",
                                      "වැඩියෙන්ම", "most supply", "most purchase"])
    wants_inactive = any(t in q for t in ["inactive", "no recent", "no transactions"])
    wants_active   = any(t in q for t in ["active", "recent"])
    wants_contact  = any(t in q for t in ["contact", "phone", "email", "details"])
    sorted_groups  = sorted(groups.items(), key=lambda x: x[1]["total"], reverse=True)

    # Apply active/inactive filter
    if wants_inactive:
        sorted_groups = [(n, s) for n, s in sorted_groups if not s["active"]]
        filter_label = "Inactive "
    elif wants_active:
        sorted_groups = [(n, s) for n, s in sorted_groups if s["active"]]
        filter_label = "Active "
    else:
        filter_label = ""

    if wants_top and sorted_groups:
        top_name, top_stats = sorted_groups[0]
        extra = []
        if top_stats.get("city"):
            extra.append(f"City: {top_stats['city'].title()}")
        if top_stats["phone"]:
            extra.append(f"Phone: {top_stats['phone']}")
        if top_stats["email"]:
            extra.append(f"Email: {top_stats['email']}")
        extra_str = ("\n" + "\n".join(extra)) if extra else ""
        answer = (
            f"Top {filter_label}Supplier: {top_name.title()}\n"
            f"Total Purchases: LKR {top_stats['total']:,.2f}\n"
            f"Transactions: {top_stats['count']}\n"
            f"Last Transaction: {top_stats['last_date']}\n"
            f"Status: {'Active' if top_stats['active'] else 'Inactive'}"
            + extra_str
        )
    elif wants_contact:
        lines = [f"{filter_label}Supplier Contact Details ({len(sorted_groups)} found):"]
        for name, stats in sorted_groups:
            phone = stats["phone"] or "N/A"
            email = stats["email"] or "N/A"
            city  = stats.get("city", "") or "N/A"
            lines.append(
                f"  {name.title()} | City: {city} | Phone: {phone} | Email: {email}"
                f" | Last: {stats['last_date']}"
            )
        answer = "\n".join(lines)
    else:
        filter_suffix = f" in {city_filter.title()}" if city_filter else ""
        lines = [f"{filter_label}Suppliers{filter_suffix} ({len(sorted_groups)} found):"]
        for name, stats in sorted_groups:
            status_tag = "Active" if stats["active"] else "Inactive"
            city_tag = f" | {stats.get('city', '').title()}" if stats.get("city") else ""
            lines.append(
                f"  {name.title()} | {status_tag}{city_tag} | Transactions: {stats['count']} "
                f"| Total: LKR {stats['total']:,.2f} | Last: {stats['last_date']}"
            )
        answer = "\n".join(lines) if sorted_groups else f"No {filter_label.lower()}suppliers found."

    total_spend = sum(v["total"] for v in groups.values())
    return {
        "success": True,
        "question_type": "supplier_query",
        "explanation": f"{filter_label}Supplier analysis for '{company_name}'.",
        "evidence": build_evidence(supplier_df.head(20), "Supplier transaction records."),
        "metrics": {
            "supplier_count": len(sorted_groups),
            "total_spend": total_spend,
            "top_supplier": sorted_groups[0][0].title() if sorted_groups else "",
            "active_count": sum(1 for _, s in groups.items() if s["active"]),
            "inactive_count": sum(1 for _, s in groups.items() if not s["active"]),
        },
        "source_file": DATASET_PATH,
        "direct_answer": answer,
    }


def handle_customer_query(question: str, df: pd.DataFrame, company_name: str):
    q = normalize_text(question)

    # Customers = receivable + cash_inflow documents (money owed to us or already received)
    customer_df = df[
        df["flow_type"].apply(normalize_flow).isin(["receivable", "cash_inflow"])
    ].copy()

    # City filter: "customers from Colombo"
    _city_filter = ""
    _city_match = re.search(r"from\s+([A-Za-z][a-zA-Z\s]{2,20})", q)
    if _city_match:
        _city_filter = normalize_text(_city_match.group(1))
        if _city_filter:
            def _cust_city_matches(row) -> bool:
                city = normalize_text(row.get("supplier_city", "") or "")
                text = normalize_text(str(row.get("corrected_text", "") or row.get("raw_text", "")))
                return _city_filter in city or _city_filter in text
            candidate = customer_df[customer_df.apply(_cust_city_matches, axis=1)].copy()
            if not candidate.empty:
                customer_df = candidate

    if customer_df.empty:
        return {
            "success": True,
            "question_type": "customer_query",
            "explanation": f"No customer records found for '{company_name}'.",
            "evidence": [],
            "metrics": {"customer_count": 0},
            "source_file": DATASET_PATH,
            "direct_answer": "No customer records found.",
        }

    entity = extract_named_entity(question)
    if entity:
        candidate = filter_named_entity(customer_df, entity)
        if not candidate.empty:
            customer_df = candidate

    _cutoff_date = (date.today() - timedelta(days=90)).strftime("%Y-%m-%d")
    today_str = date.today().strftime("%Y-%m-%d")

    groups = {}
    for _, row in customer_df.iterrows():
        name = normalize_text(row.get("supplier_name", "Unknown")) or "Unknown"
        row_date = str(row.get("date", "")) or ""
        if name not in groups:
            groups[name] = {
                "total": 0.0, "paid": 0.0, "count": 0,
                "last_date": "", "first_date": row_date,
                "phone": "", "email": "", "city": "",
            }
        amount = preferred_amount(row)
        groups[name]["total"] += amount
        if normalize_text(row.get("paid_status", "")) == "paid":
            groups[name]["paid"] += amount
        groups[name]["count"] += 1
        if row_date > groups[name]["last_date"]:
            groups[name]["last_date"] = row_date
        if row_date and (not groups[name]["first_date"] or row_date < groups[name]["first_date"]):
            groups[name]["first_date"] = row_date
        if not groups[name]["phone"] and not groups[name]["email"]:
            contact = _extract_contact_from_row(row)
            groups[name]["phone"] = contact.get("phone", "")
            groups[name]["email"] = contact.get("email", "")
            if not groups[name].get("city") and contact.get("city"):
                groups[name]["city"] = contact["city"]

    # Enrich with active/inactive flag
    for name, stats in groups.items():
        stats["active"] = stats["last_date"] >= _cutoff_date if stats["last_date"] else False
        # "new today" = first ever transaction is today
        stats["new_today"] = stats["first_date"] == today_str

    wants_top      = any(t in q for t in ["top", "highest", "most", "biggest", "who bought most",
                                           "who owes most", "most", "වැඩියෙන්ම"])
    wants_inactive = any(t in q for t in ["inactive", "no recent"])
    wants_active   = any(t in q for t in ["active", "recent"])
    wants_new      = any(t in q for t in ["new today", "new customers", "today's new",
                                           "registered today", "first time"])
    wants_contact  = any(t in q for t in ["contact", "phone", "email", "details"])
    wants_balance  = any(t in q for t in ["balance", "owes most", "who owes", "outstanding"])
    sorted_groups  = sorted(groups.items(), key=lambda x: x[1]["total"], reverse=True)

    # Apply sub-filters
    filter_label = ""
    if wants_new:
        sorted_groups = [(n, s) for n, s in sorted_groups if s["new_today"]]
        filter_label = "New (Today) "
    elif wants_inactive:
        sorted_groups = [(n, s) for n, s in sorted_groups if not s["active"]]
        filter_label = "Inactive "
    elif wants_active:
        sorted_groups = [(n, s) for n, s in sorted_groups if s["active"]]
        filter_label = "Active "

    # Sort by outstanding balance when requested
    if wants_balance:
        sorted_groups = sorted(sorted_groups, key=lambda x: x[1]["total"] - x[1]["paid"], reverse=True)

    if wants_top and sorted_groups:
        top_name, top_stats = sorted_groups[0]
        outstanding = top_stats["total"] - top_stats["paid"]
        contact_line = ""
        if top_stats["phone"]:
            contact_line += f"\nPhone: {top_stats['phone']}"
        if top_stats["email"]:
            contact_line += f"\nEmail: {top_stats['email']}"
        answer = (
            f"Top {filter_label}Customer: {top_name.title()}\n"
            f"Total Invoiced: LKR {top_stats['total']:,.2f}\n"
            f"Outstanding Balance: LKR {outstanding:,.2f}\n"
            f"Transactions: {top_stats['count']}\n"
            f"Last: {top_stats['last_date']}\n"
            f"Status: {'Active' if top_stats['active'] else 'Inactive'}"
            + contact_line
        )
    elif wants_contact:
        lines = [f"{filter_label}Customer Contact Details ({len(sorted_groups)} found):"]
        for name, stats in sorted_groups:
            phone = stats["phone"] or "N/A"
            email = stats["email"] or "N/A"
            lines.append(f"  {name.title()} | Phone: {phone} | Email: {email}")
        answer = "\n".join(lines)
    else:
        lines = [f"{filter_label}Customers ({len(sorted_groups)} found):"]
        for name, stats in sorted_groups:
            outstanding = stats["total"] - stats["paid"]
            status_tag = "Active" if stats["active"] else "Inactive"
            lines.append(
                f"  {name.title()} | {status_tag} | Transactions: {stats['count']} "
                f"| Total: LKR {stats['total']:,.2f} | Outstanding: LKR {outstanding:,.2f}"
            )
        answer = "\n".join(lines) if sorted_groups else f"No {filter_label.lower()}customers found."

    total_receivable = sum(v["total"] - v["paid"] for v in groups.values())
    return {
        "success": True,
        "question_type": "customer_query",
        "explanation": f"{filter_label}Customer analysis for '{company_name}'.",
        "evidence": build_evidence(customer_df.head(20), "Customer transaction records."),
        "metrics": {
            "customer_count": len(sorted_groups),
            "total_receivable": total_receivable,
            "top_customer": sorted_groups[0][0].title() if sorted_groups else "",
            "active_count": sum(1 for _, s in groups.items() if s["active"]),
            "inactive_count": sum(1 for _, s in groups.items() if not s["active"]),
            "new_today_count": sum(1 for _, s in groups.items() if s["new_today"]),
        },
        "source_file": DATASET_PATH,
        "direct_answer": answer,
    }


def handle_cross_document_query(question: str, df: pd.DataFrame, company_name: str):
    doc_id = _extract_doc_id_from_question(question)
    if not doc_id:
        return None

    # Match via order_id OR document_id
    mask = (
        df["order_id"].apply(normalize_text).str.contains(normalize_text(doc_id), na=False)
        | df["document_id"].apply(normalize_text).str.contains(normalize_text(doc_id), na=False)
    )
    result_df = df[mask].copy()

    if result_df.empty:
        return {
            "success": True,
            "question_type": "cross_document_query",
            "explanation": f"No documents found linked to '{doc_id}'.",
            "evidence": [],
            "metrics": {"linked_count": 0},
            "source_file": DATASET_PATH,
            "direct_answer": f"No documents found related to '{doc_id}'.",
        }

    grouped = {}
    for _, row in result_df.iterrows():
        dt = str(row.get("document_type", "unknown")).upper()
        grouped.setdefault(dt, []).append(row)

    lines = [f"Documents related to '{doc_id}' ({len(result_df)} found):"]
    for doc_type, rows in grouped.items():
        lines.append(f"\n  {doc_type} ({len(rows)} document(s)):")
        for row in rows:
            currency = str(row.get("currency", "LKR")).strip() or "LKR"
            amount = preferred_amount(row)
            lines.append(
                f"    {row.get('document_id','?')} | {row.get('supplier_name','?')} "
                f"| {row.get('date','?')} | {currency} {amount:,.2f}"
            )

    return {
        "success": True,
        "question_type": "cross_document_query",
        "explanation": f"Documents linked to '{doc_id}' via order ID or document ID.",
        "evidence": build_evidence(result_df, f"Linked to document/order '{doc_id}'."),
        "metrics": {
            "linked_count": len(result_df),
            "doc_types": {k: len(v) for k, v in grouped.items()},
        },
        "source_file": DATASET_PATH,
        "direct_answer": "\n".join(lines),
    }


def handle_count_query(question: str, df: pd.DataFrame, company_name: str):
    q = normalize_text(question)

    if any(t in q for t in ["invoice", "invoices", "ඉන්වොයිස්"]):
        result_df = df[df["document_type"].apply(normalize_text) == "invoice"]
        doc_label = "invoice"
    elif any(t in q for t in ["po", "purchase order", "ඇණවුම"]):
        result_df = df[df["document_type"].apply(normalize_text) == "po"]
        doc_label = "purchase order"
    elif any(t in q for t in ["dn", "delivery note", "deliveries", "delivery", "බෙදාහැරීම"]):
        result_df = df[df["document_type"].apply(normalize_text) == "dn"]
        doc_label = "delivery note"
    elif any(t in q for t in ["receipt", "receipts", "රිසිට්"]):
        result_df = df[df["document_type"].apply(normalize_text) == "receipt"]
        doc_label = "receipt"
    else:
        result_df = df
        doc_label = "document"

    # Apply date filter if present
    date_from, date_to = parse_temporal_filter(question)
    if date_from or date_to:
        result_df = apply_date_filter(result_df, date_from, date_to)

    count = len(result_df)
    period_str = ""
    if date_from and date_to:
        period_str = f" from {date_from} to {date_to}"

    return {
        "success": True,
        "question_type": "count_query",
        "explanation": f"Count of {doc_label}s{period_str} for '{company_name}'.",
        "evidence": build_evidence(result_df.head(20), f"Count query: {doc_label}s{period_str}."),
        "metrics": {"count": count, "doc_type": doc_label, "period": period_str.strip()},
        "source_file": DATASET_PATH,
        "direct_answer": f"You have {count} {doc_label}(s){period_str}.",
    }


def handle_financial_comparison(question: str, df: pd.DataFrame, company_name: str):
    today = date.today()

    # Current month
    cur_start = today.replace(day=1)
    cur_end = today
    cur_df = apply_date_filter(df, cur_start, cur_end)
    cur_total = sum_amounts(cur_df)

    # Previous month
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    prev_df = apply_date_filter(df, prev_start, prev_end)
    prev_total = sum_amounts(prev_df)

    change = cur_total - prev_total
    change_pct = (change / prev_total * 100) if prev_total > 0 else 0.0

    # Revenue vs expenses for profit
    rev_df = cur_df[cur_df["flow_type"].apply(normalize_flow).isin(["receivable", "cash_inflow"])]
    exp_df = cur_df[cur_df["flow_type"].apply(normalize_flow).isin(["payable", "cash_outflow"])]
    revenue = sum_amounts(rev_df)
    expenses = sum_amounts(exp_df)
    profit = revenue - expenses

    answer = (
        f"Financial Comparison — {cur_start.strftime('%B %Y')} vs {prev_start.strftime('%B %Y')}\n\n"
        f"This Month:\n"
        f"  Total: LKR {cur_total:,.2f}\n"
        f"  Revenue: LKR {revenue:,.2f}\n"
        f"  Expenses: LKR {expenses:,.2f}\n"
        f"  Profit/Loss: LKR {profit:+,.2f}\n\n"
        f"Last Month Total: LKR {prev_total:,.2f}\n"
        f"Change: LKR {change:+,.2f} ({change_pct:+.1f}%)"
    )

    return {
        "success": True,
        "question_type": "financial_comparison",
        "explanation": f"Month-over-month comparison for '{company_name}'.",
        "evidence": build_evidence(cur_df.head(20), "Current month documents."),
        "metrics": {
            "current_month_total": cur_total,
            "previous_month_total": prev_total,
            "change": change,
            "change_pct": round(change_pct, 1),
            "revenue": revenue,
            "expenses": expenses,
            "profit": profit,
        },
        "source_file": DATASET_PATH,
        "direct_answer": answer,
    }


def handle_activity_query(question: str, df: pd.DataFrame, company_name: str):
    q = normalize_text(question)
    date_from, date_to = parse_temporal_filter(question)
    if date_from is None:
        # Default: today
        date_from = date_to = date.today()

    result_df = apply_date_filter(df, date_from, date_to)
    result_df = result_df.sort_values("date", ascending=False) if "date" in result_df.columns else result_df

    period_label = str(date_from) if date_from == date_to else f"{date_from} to {date_to}"
    count = len(result_df)

    lines = [f"{count} document(s) created {period_label}:"]
    for _, row in result_df.iterrows():
        dt = str(row.get("document_type", "?")).upper()
        supplier = row.get("supplier_name", "?")
        currency = str(row.get("currency", "LKR")).strip() or "LKR"
        amount = preferred_amount(row)
        lines.append(
            f"  {row.get('document_id','?')} ({dt}) | {supplier} "
            f"| {row.get('date','?')} | {currency} {amount:,.2f}"
        )

    return {
        "success": True,
        "question_type": "activity_query",
        "explanation": f"Documents created on {period_label} for '{company_name}'.",
        "evidence": build_evidence(result_df, f"Activity on {period_label}."),
        "metrics": {"document_count": count, "period": period_label},
        "source_file": DATASET_PATH,
        "direct_answer": "\n".join(lines) if not result_df.empty else f"No documents found for {period_label}.",
    }


def handle_payment_query(question: str, df: pd.DataFrame, company_name: str):
    q = normalize_text(question)

    # Received payments = cash_inflow docs + receivable docs with paid/received status
    if any(t in q for t in ["received payment", "payments received", "who paid",
                              "ලැබුණු payments", "paid us", "have paid"]):
        result_df = df[
            df["flow_type"].apply(normalize_flow).isin(["cash_inflow"])
            | (df["flow_type"].apply(normalize_flow).isin(["receivable"])
               & df["paid_status"].apply(normalize_text).isin(["paid", "partial"]))
        ].copy()
        label = "Received Payments"
    elif any(t in q for t in ["pending payment", "pending payments", "who hasn't paid",
                                "ගෙවලා නැති", "not paid yet", "outstanding payment"]):
        result_df = df[
            df["flow_type"].apply(normalize_flow).isin(["receivable"])
            & df["paid_status"].apply(normalize_text).isin(["not_paid", ""])
        ].copy()
        label = "Pending Payments"
    else:
        result_df = df[
            df["flow_type"].apply(normalize_flow).isin(["receivable", "cash_inflow"])
        ].copy()
        label = "Payment History"

    entity = extract_named_entity(question)
    if entity:
        candidate = filter_named_entity(result_df, entity)
        if not candidate.empty:
            result_df = candidate

    date_from, date_to = parse_temporal_filter(question)
    if date_from or date_to:
        result_df = apply_date_filter(result_df, date_from, date_to)

    total = sum_amounts(result_df)
    lines = [f"{label} ({len(result_df)} records):"]
    for _, row in result_df.iterrows():
        currency = str(row.get("currency", "LKR")).strip() or "LKR"
        amount = preferred_amount(row)
        status = str(row.get("paid_status", row.get("received_status", ""))).replace("_", " ").title()
        lines.append(
            f"  {row.get('document_id','?')} | {row.get('supplier_name','?')} "
            f"| {row.get('date','?')} | {currency} {amount:,.2f} | {status}"
        )
    if total > 0:
        lines.append(f"Total: LKR {total:,.2f}")

    return {
        "success": True,
        "question_type": "payment_query",
        "explanation": f"{label} for '{company_name}'.",
        "evidence": build_evidence(result_df, f"{label} records."),
        "metrics": {"payment_count": len(result_df), "total_amount": total, "label": label},
        "source_file": DATASET_PATH,
        "direct_answer": "\n".join(lines) if not result_df.empty else f"No {label.lower()} found.",
    }


def handle_monthly_breakdown(question: str, df: pd.DataFrame, company_name: str):
    """Revenue/expenses broken down month by month for the current year."""
    q = normalize_text(question)
    today = date.today()
    year = today.year

    # Optionally filter to revenue or expenses only
    wants_revenue = any(t in q for t in ["revenue", "income", "sales", "receivable"])
    wants_expenses = any(t in q for t in ["expense", "expenses", "purchase", "payable", "cost"])

    if wants_revenue and not wants_expenses:
        scope_df = df[df["flow_type"].apply(normalize_flow).isin(["receivable", "cash_inflow"])].copy()
        scope_label = "Revenue"
    elif wants_expenses and not wants_revenue:
        scope_df = df[df["flow_type"].apply(normalize_flow).isin(["payable", "cash_outflow"])].copy()
        scope_label = "Expenses"
    else:
        scope_df = df.copy()
        scope_label = "Total"

    monthly: dict[int, float] = {m: 0.0 for m in range(1, 13)}
    for _, row in scope_df.iterrows():
        d = _parse_date_for_filter(row.get("date", ""))
        if d and d.year == year:
            monthly[d.month] += preferred_amount(row)

    lines = [f"{scope_label} by Month — {year}:"]
    for m in range(1, 13):
        month_name = date(year, m, 1).strftime("%B")
        if monthly[m] > 0 or m <= today.month:
            lines.append(f"  {month_name:12s}: LKR {monthly[m]:>12,.2f}")

    total = sum(monthly.values())
    lines.append(f"\n  {'Year Total':12s}: LKR {total:>12,.2f}")

    return {
        "success": True,
        "question_type": "monthly_breakdown",
        "explanation": f"{scope_label} breakdown by month for {year}, company '{company_name}'.",
        "evidence": build_evidence(scope_df.head(20), f"Monthly {scope_label} breakdown."),
        "metrics": {
            "year": year,
            "scope": scope_label,
            "monthly_totals": {date(year, m, 1).strftime("%B"): round(v, 2) for m, v in monthly.items()},
            "year_total": round(total, 2),
        },
        "source_file": DATASET_PATH,
        "direct_answer": "\n".join(lines),
    }


def handle_ambiguous_status_query(question: str, df: pd.DataFrame, company_name: str):
    """
    Handle vague queries like 'Anything overdue?' or 'What's still pending?'
    by checking across all document types simultaneously.
    """
    q = normalize_text(question)
    today = date.today()
    sections = []
    evidence_rows = []

    wants_overdue = any(t in q for t in ["overdue", "late", "past due", "missed"])
    wants_pending = any(t in q for t in ["pending", "outstanding", "open", "left", "still"])

    # Overdue invoices
    inv_df = df[df["document_type"].apply(normalize_text) == "invoice"].copy()
    if wants_overdue or not wants_pending:
        overdue_inv = inv_df[
            inv_df["invoice_status"].apply(lambda x: str(x).lower() == "overdue").fillna(False)
        ]
        if not overdue_inv.empty:
            total = sum_amounts(overdue_inv)
            sections.append(f"Overdue Invoices ({len(overdue_inv)}): LKR {total:,.2f}")
            evidence_rows.append(overdue_inv)

    # Pending POs
    po_df = df[df["document_type"].apply(normalize_text) == "po"].copy()
    if wants_pending or not wants_overdue:
        pending_po = po_df[
            po_df["po_status"].apply(lambda x: str(x).lower() in ("pending", "approved") if x and str(x).upper() not in ("NULL","NONE") else True)
        ]
        if not pending_po.empty:
            total = sum_amounts(pending_po)
            sections.append(f"Open Purchase Orders ({len(pending_po)}): LKR {total:,.2f}")
            evidence_rows.append(pending_po)

    # Overdue/pending POs by delivery date
    if wants_overdue:
        overdue_po = po_df[po_df.apply(
            lambda r: (str(r.get("po_status", "")).lower() not in ("fulfilled", "cancelled"))
                      and bool(_parse_date_for_filter(r.get("delivery_date", "")) and
                               _parse_date_for_filter(r.get("delivery_date", "")) < today),
            axis=1
        )]
        if not overdue_po.empty:
            sections.append(f"Overdue Purchase Orders ({len(overdue_po)}): delivery date passed")
            evidence_rows.append(overdue_po)

    # Delayed deliveries
    dn_df = df[df["document_type"].apply(normalize_text) == "dn"].copy()
    delayed_dn = dn_df[
        dn_df["dn_status"].apply(lambda x: str(x).lower() in ("pending", "delayed") if x and str(x).upper() not in ("NULL","NONE") else True)
    ]
    if not delayed_dn.empty:
        sections.append(f"Pending/Delayed Deliveries ({len(delayed_dn)})")
        evidence_rows.append(delayed_dn)

    if not sections:
        answer = "No overdue or pending documents found. Everything is up to date."
    else:
        answer = "Summary of Outstanding Items:\n" + "\n".join(f"  • {s}" for s in sections)

    combined_df = pd.concat(evidence_rows, ignore_index=True).drop_duplicates("document_id") if evidence_rows else df.iloc[0:0]

    return {
        "success": True,
        "question_type": "ambiguous_status_query",
        "explanation": f"Cross-document overdue/pending check for '{company_name}'.",
        "evidence": build_evidence(combined_df.head(20), "Multi-type status check."),
        "metrics": {
            "sections_found": len(sections),
            "total_items": len(combined_df),
        },
        "source_file": DATASET_PATH,
        "direct_answer": answer,
    }


def analyze_financial_query(question: str, company_name: str, user_id: str):
    df = load_dataset(user_id=user_id)
    user_df = filter_user_context(df, user_id=user_id)

    if user_df.empty:
        return {
            "success": False,
            "question_type": "none",
            "explanation": "No saved records found for the current user.",
            "evidence": [],
            "metrics": {},
            "source_file": DATASET_PATH,
        }

    company_df = filter_company_context(user_df, company_name)

    if company_df.empty:
        return {
            "success": False,
            "question_type": "none",
            "explanation": f"No records found for company '{company_name}' under the current user.",
            "evidence": [],
            "metrics": {},
            "source_file": DATASET_PATH,
        }

    # Step 1: fuzzy spell-correct English financial terms (e.g. "payahles" → "payables")
    spell_corrected, spell_corrections = spell_correct_query(question)
    # Step 2: normalize Sinhala/English mixed phrases
    normalized_question = normalize_query(spell_corrected)
    question_type = route_question(normalized_question)
    entity_name = extract_named_entity(normalized_question)

    # ── Iteration 10: new intent handlers ────────────────────────────────────
    nq = normalized_question  # shorter alias
    # Pass spell correction note forward so answer can surface it
    _spell_note = (
        f"(Auto-corrected: {', '.join(spell_corrections)})" if spell_corrections else ""
    )

    def _with_spell_note(result: dict) -> dict:
        """Append spell correction note to direct_answer and explanation if present."""
        if not _spell_note:
            return result
        r = dict(result)
        if r.get("direct_answer"):
            r["direct_answer"] = f"{_spell_note}\n\n{r['direct_answer']}"
        if r.get("explanation"):
            r["explanation"] = f"{r['explanation']} {_spell_note}"
        if isinstance(r.get("metrics"), dict):
            r["metrics"] = {**r["metrics"], "spell_corrections": spell_corrections}
        return r

    if question_type == "document_lookup":
        result = handle_document_lookup(nq, company_df, company_name)
        if result:
            return _with_spell_note(result)
        question_type = "summary"

    if question_type == "po_status_query":
        return _with_spell_note(handle_po_status_query(nq, company_df, company_name))

    if question_type == "invoice_status_query":
        return _with_spell_note(handle_invoice_status_query(nq, company_df, company_name))

    if question_type == "dn_status_query":
        return _with_spell_note(handle_dn_status_query(nq, company_df, company_name))

    if question_type == "date_range_query":
        return _with_spell_note(handle_date_range_query(nq, company_df, company_name))

    if question_type == "supplier_query":
        return _with_spell_note(handle_supplier_query(nq, company_df, company_name))

    if question_type == "customer_query":
        return _with_spell_note(handle_customer_query(nq, company_df, company_name))

    if question_type == "cross_document_query":
        result = handle_cross_document_query(nq, company_df, company_name)
        if result:
            return _with_spell_note(result)
        question_type = "summary"

    if question_type == "count_query":
        return _with_spell_note(handle_count_query(nq, company_df, company_name))

    if question_type == "financial_comparison":
        return _with_spell_note(handle_financial_comparison(nq, company_df, company_name))

    if question_type == "activity_query":
        return _with_spell_note(handle_activity_query(nq, company_df, company_name))

    if question_type == "payment_query":
        return _with_spell_note(handle_payment_query(nq, company_df, company_name))

    if question_type == "monthly_breakdown":
        return _with_spell_note(handle_monthly_breakdown(nq, company_df, company_name))

    if question_type == "ambiguous_status_query":
        return _with_spell_note(handle_ambiguous_status_query(nq, company_df, company_name))
    # ─────────────────────────────────────────────────────────────────────────

    filtered_df = company_df.copy()
    entity_filter_applied = False

    if entity_name:
        candidate_df = filter_named_entity(company_df, entity_name)
        if not candidate_df.empty:
            filtered_df = candidate_df
            entity_filter_applied = True

    # Revenue = receivable + cash_inflow
    if question_type == "revenue":
        result_df = filtered_df[
            filtered_df["flow_type"].apply(normalize_flow).isin(["receivable", "cash_inflow"])
        ].copy()
        total_amount = sum_amounts(result_df)
        reason = "Included because flow_type is receivable or cash_inflow (Revenue category)."
        return {
            "success": True,
            "question_type": "revenue",
            "direct_answer": build_direct_answer(result_df, "revenue", company_name, question),
            "explanation": f"Total revenue (receivable + cash inflow) for '{company_name}'." + (f" Entity filter: '{entity_name}'." if entity_filter_applied else ""),
            "evidence": build_evidence(result_df, reason),
            "metrics": {
                "company_name": company_name,
                "matching_records": int(len(result_df)),
                "revenue_documents": int(len(result_df)),
                "entity_filter": entity_name if entity_filter_applied else "",
                "total_revenue_amount": total_amount,
            },
            "source_file": DATASET_PATH,
        }

    # Expenses = payable + cash_outflow
    if question_type == "expenses":
        result_df = filtered_df[
            filtered_df["flow_type"].apply(normalize_flow).isin(["payable", "cash_outflow"])
        ].copy()
        total_amount = sum_amounts(result_df)
        reason = "Included because flow_type is payable or cash_outflow (Expenses category)."
        return {
            "success": True,
            "question_type": "expenses",
            "direct_answer": build_direct_answer(result_df, "expenses", company_name, question),
            "explanation": f"Total expenses (payable + cash outflow) for '{company_name}'." + (f" Entity filter: '{entity_name}'." if entity_filter_applied else ""),
            "evidence": build_evidence(result_df, reason),
            "metrics": {
                "company_name": company_name,
                "matching_records": int(len(result_df)),
                "expense_documents": int(len(result_df)),
                "entity_filter": entity_name if entity_filter_applied else "",
                "total_expenses_amount": total_amount,
            },
            "source_file": DATASET_PATH,
        }

    # Cash inflow = cash_inflow docs + receivable with cash_inflowed > 0
    if question_type == "cash_inflow":
        inflow_df = filtered_df[filtered_df["flow_type"].apply(normalize_flow) == "cash_inflow"].copy()
        partial_df = filtered_df[
            (filtered_df["flow_type"].apply(normalize_flow) == "receivable") &
            (filtered_df.get("cash_inflowed", pd.Series(dtype=float)).fillna(0) > 0)
        ].copy() if "cash_inflowed" in filtered_df.columns else filtered_df.iloc[0:0].copy()
        result_df = pd.concat([inflow_df, partial_df], ignore_index=True).drop_duplicates(subset=["document_id"])
        total_amount = sum_amounts(result_df)
        reason = "Included because flow_type is cash_inflow or has partial cash inflowed amount."
        return {
            "success": True,
            "question_type": "cash_inflow",
            "direct_answer": build_direct_answer(result_df, "cash_inflow", company_name, question),
            "explanation": f"Total cash inflow for '{company_name}' (fully received + partially received receivables).",
            "evidence": build_evidence(result_df, reason),
            "metrics": {
                "company_name": company_name,
                "cash_inflow_documents": int(len(result_df)),
                "total_cash_inflow_amount": total_amount,
            },
            "source_file": DATASET_PATH,
        }

    # Cash outflow = cash_outflow docs + payable with cash_outflowed > 0
    if question_type == "cash_outflow":
        outflow_df = filtered_df[filtered_df["flow_type"].apply(normalize_flow) == "cash_outflow"].copy()
        partial_df = filtered_df[
            (filtered_df["flow_type"].apply(normalize_flow) == "payable") &
            (filtered_df.get("cash_outflowed", pd.Series(dtype=float)).fillna(0) > 0)
        ].copy() if "cash_outflowed" in filtered_df.columns else filtered_df.iloc[0:0].copy()
        result_df = pd.concat([outflow_df, partial_df], ignore_index=True).drop_duplicates(subset=["document_id"])
        total_amount = sum_amounts(result_df)
        reason = "Included because flow_type is cash_outflow or has partial cash outflowed amount."
        return {
            "success": True,
            "question_type": "cash_outflow",
            "direct_answer": build_direct_answer(result_df, "cash_outflow", company_name, question),
            "explanation": f"Total cash outflow for '{company_name}' (fully paid + partially paid payables).",
            "evidence": build_evidence(result_df, reason),
            "metrics": {
                "company_name": company_name,
                "cash_outflow_documents": int(len(result_df)),
                "total_cash_outflow_amount": total_amount,
            },
            "source_file": DATASET_PATH,
        }

    if question_type == "receivable":
        receivable_df = filtered_df[filtered_df["flow_type"].apply(normalize_flow) == "receivable"].copy()

        if "received_status" in receivable_df.columns:
            outstanding_df = receivable_df[
                receivable_df["received_status"].apply(normalize_text) != "received"
            ].copy()
        else:
            outstanding_df = receivable_df.copy()

        total_amount = sum_amounts(outstanding_df)

        reason = "Included because current user matched, company matched, and flow_type is receivable."
        if entity_filter_applied:
            reason = f"Included because current user matched, company matched, flow_type is receivable, and entity matched '{entity_name}'."

        return {
            "success": True,
            "question_type": "receivable",
            "explanation": (
                f"Computed receivable amount for company '{company_name}' using only the current user's receivable documents."
                + (f" Applied extra entity filter for '{entity_name}'." if entity_filter_applied else "")
            ),
            "evidence": build_evidence(outstanding_df, reason),
            "metrics": {
                "company_name": company_name,
                "matching_records": int(len(company_df)),
                "filtered_records": int(len(filtered_df)),
                "receivable_documents": int(len(outstanding_df)),
                "entity_filter": entity_name if entity_filter_applied else "",
                "total_receivable_amount": total_amount,
            },
            "source_file": DATASET_PATH,
            "direct_answer": build_direct_answer(outstanding_df, "receivable", company_name, question),
        }

    if question_type == "payable":
        payable_df = filtered_df[filtered_df["flow_type"].apply(normalize_flow) == "payable"].copy()

        if "paid_status" in payable_df.columns:
            outstanding_df = payable_df[
                payable_df["paid_status"].apply(normalize_text) != "paid"
            ].copy()
        else:
            outstanding_df = payable_df.copy()

        total_amount = sum_amounts(outstanding_df)

        reason = "Included because current user matched, company matched, and flow_type is payable."
        if entity_filter_applied:
            reason = f"Included because current user matched, company matched, flow_type is payable, and entity matched '{entity_name}'."

        return {
            "success": True,
            "question_type": "payable",
            "explanation": (
                f"Computed payable amount for company '{company_name}' using only the current user's payable documents."
                + (f" Applied extra entity filter for '{entity_name}'." if entity_filter_applied else "")
            ),
            "evidence": build_evidence(outstanding_df, reason),
            "metrics": {
                "company_name": company_name,
                "matching_records": int(len(company_df)),
                "filtered_records": int(len(filtered_df)),
                "payable_documents": int(len(outstanding_df)),
                "entity_filter": entity_name if entity_filter_applied else "",
                "total_payable_amount": total_amount,
            },
            "source_file": DATASET_PATH,
            "direct_answer": build_direct_answer(outstanding_df, "payable", company_name, question),
        }

    if question_type == "invoice_list":
        result_df = filtered_df[filtered_df["document_type"].apply(normalize_text) == "invoice"].copy()
        min_amt, max_amt = parse_amount_filter(nq)
        result_df = apply_amount_filter(result_df, min_amt, max_amt)
        return {
            "success": True,
            "question_type": "invoice_list",
            "explanation": f"Listed invoices for company '{company_name}' using only the current user's records.",
            "evidence": build_evidence(
                result_df,
                "Included because current user matched, company matched, and document_type is invoice."
            ),
            "metrics": {
                "company_name": company_name,
                "invoice_count": int(len(result_df)),
                "entity_filter": entity_name if entity_filter_applied else "",
                "amount_filter": f"min={min_amt}, max={max_amt}" if (min_amt or max_amt) else "",
            },
            "source_file": DATASET_PATH,
            "direct_answer": build_direct_answer(result_df, "invoice_list", company_name, question),
        }

    if question_type == "receipt_list":
        result_df = filtered_df[filtered_df["document_type"].apply(normalize_text) == "receipt"].copy()
        return {
            "success": True,
            "question_type": "receipt_list",
            "explanation": f"Listed receipts for company '{company_name}' using only the current user's records.",
            "evidence": build_evidence(
                result_df,
                "Included because current user matched, company matched, and document_type is receipt."
            ),
            "metrics": {
                "company_name": company_name,
                "receipt_count": int(len(result_df)),
                "entity_filter": entity_name if entity_filter_applied else "",
            },
            "source_file": DATASET_PATH,
            "direct_answer": build_direct_answer(result_df, "receipt_list", company_name, question),
        }

    if question_type == "po_list":
        result_df = filtered_df[filtered_df["document_type"].apply(normalize_text) == "po"].copy()
        min_amt, max_amt = parse_amount_filter(nq)
        result_df = apply_amount_filter(result_df, min_amt, max_amt)
        return {
            "success": True,
            "question_type": "po_list",
            "explanation": f"Listed purchase orders for company '{company_name}' using only the current user's records.",
            "evidence": build_evidence(
                result_df,
                "Included because current user matched, company matched, and document_type is po."
            ),
            "metrics": {
                "company_name": company_name,
                "po_count": int(len(result_df)),
                "entity_filter": entity_name if entity_filter_applied else "",
                "amount_filter": f"min={min_amt}, max={max_amt}" if (min_amt or max_amt) else "",
            },
            "source_file": DATASET_PATH,
            "direct_answer": build_direct_answer(result_df, "po_list", company_name, question),
        }

    if question_type == "dn_list":
        result_df = filtered_df[filtered_df["document_type"].apply(normalize_text) == "dn"].copy()
        return {
            "success": True,
            "question_type": "dn_list",
            "explanation": f"Listed delivery notes for company '{company_name}' using only the current user's records.",
            "evidence": build_evidence(
                result_df,
                "Included because current user matched, company matched, and document_type is dn."
            ),
            "metrics": {
                "company_name": company_name,
                "dn_count": int(len(result_df)),
                "entity_filter": entity_name if entity_filter_applied else "",
            },
            "source_file": DATASET_PATH,
            "direct_answer": build_direct_answer(result_df, "dn_list", company_name, question),
        }

    result_df = filtered_df.copy()
    return _with_spell_note({
        "success": True,
        "question_type": "summary",
        "explanation": f"Generated a summary using only the current user's matching records for company '{company_name}'.",
        "evidence": build_evidence(
            result_df,
            "Included because current user matched and company matched."
            + (f" Entity filter '{entity_name}' was also applied." if entity_filter_applied else "")
        ),
        "metrics": {
            "company_name": company_name,
            "matching_records": int(len(company_df)),
            "filtered_records": int(len(filtered_df)),
            "entity_filter": entity_name if entity_filter_applied else "",
        },
        "source_file": DATASET_PATH,
        "direct_answer": build_direct_answer(result_df, "summary", company_name, question),
    })


# ─────────────────── GAP-19A: cross-document price discrepancy ───────────────


def _desc_similarity(a: str, b: str) -> float:
    """Simple word-overlap similarity for matching line-item descriptions."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def find_price_discrepancies(invoice_items: list[dict], po_items: list[dict]) -> list[dict]:
    """
    GAP-19A (Iter 19): compare unit prices between an invoice and its linked PO.
    Returns a list of discrepancy dicts for items where unit_price differs by >2%.
    """
    results = []
    for inv in invoice_items:
        inv_price = to_float(inv.get("unit_price", 0))
        inv_desc = str(inv.get("description") or "").strip()
        if inv_price <= 0 or not inv_desc:
            continue
        for po in po_items:
            po_price = to_float(po.get("unit_price", 0))
            po_desc = str(po.get("description") or "").strip()
            if po_price <= 0 or not po_desc:
                continue
            if _desc_similarity(inv_desc, po_desc) >= 0.5:
                diff_pct = ((inv_price - po_price) / po_price) * 100
                results.append({
                    "description": inv_desc,
                    "invoice_price": round(inv_price, 2),
                    "po_price": round(po_price, 2),
                    "diff_pct": round(diff_pct, 1),
                    "is_discrepancy": abs(diff_pct) > 2.0,
                    "direction": "higher" if diff_pct > 0 else "lower",
                })
                break
    return results


def detect_discrepancies_in_evidence(evidence: list[dict]) -> list[dict]:
    """
    Check if evidence contains both an invoice and a PO — if so, compare line-item prices.
    Returns the discrepancy list (may be empty).
    """
    invoices = [e for e in evidence if str(e.get("document_type", "")).lower() == "invoice"]
    pos = [e for e in evidence if str(e.get("document_type", "")).lower() == "po"]
    if not invoices or not pos:
        return []
    inv_items = invoices[0].get("items") or []
    po_items = pos[0].get("items") or []
    return find_price_discrepancies(inv_items, po_items)