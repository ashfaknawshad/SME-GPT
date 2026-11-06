"""Component 3 — orchestrator: Scope Resolver -> Planner -> Validator ->
Executor -> Answer Generator (docs/components/component-3.md "Flow"), wired
into the live /ask-query endpoint (app.py).

Falls back to the pre-PAL ad-hoc logic (data_tools.analyze_financial_query +
ai_helper.generate_explainable_answer) whenever PAL can't confidently
answer: the query LLM is unavailable, the plan never validates within the retry
budget, the question doesn't map to one of PAL's arithmetic tasks (e.g.
"list my invoices"), or the validated plan matched zero rows. This keeps
every existing live query working while adding hallucination-free
arithmetic with citations for the cases PAL covers -- the same
"deterministic fallback when the LLM can't be trusted" philosophy as
ocr_correction.safe_correct() and ai_helper.build_fallback_answer.
"""
from __future__ import annotations

import data_tools as dt
from ai_helper import generate_explainable_answer
from financial_advisor import continues_advisory_conversation, generate_financial_advice
from llm_client import call_llm
from pal_answer import generate_pal_answer
from pal_executor import execute_plan
from pal_planner import plan_query
from pal_scope import resolve_scope, resolve_scope_with_rag, rows_for_plan
from pal_validator import validate_plan

MAX_RETRIES = 2
SOURCE = "FinancialDocument + LineItem (Postgres)"

# Listing-style intents aren't arithmetic questions -- skip straight to the
# legacy path rather than spending a planner call that can't help.
_NON_ARITHMETIC_INTENTS = {
    "invoice_list", "receipt_list", "po_list", "dn_list",
    # Iteration 10/11: all new deterministic handlers — never need PAL
    "document_lookup",
    "po_status_query", "invoice_status_query", "dn_status_query",
    "date_range_query",
    "supplier_query", "customer_query",
    "cross_document_query",
    "count_query",
    "activity_query",
    "payment_query",
    "monthly_breakdown",
    "ambiguous_status_query",
}

# These intents have deterministic handlers in data_tools.analyze_financial_query
# that correctly map the new flow types. Bypassing the PAL planner avoids the
# LLM generating wrong flow_type filters for these category-level questions.
# Note: "receivable" and "payable" stay in the PAL path (existing tests cover them).
_LEGACY_DIRECT_INTENTS = {
    "revenue", "expenses", "cash_inflow", "cash_outflow",
    # financial_comparison has its own deterministic handler too
    "financial_comparison",
}

# Intents whose income/expense aggregations we prefer to answer with PAL instead
# of the brittle keyword handlers. Routing (route_question) grabs "how much did we
# earn this month" as date_range_query because of "this month"; the legacy handler
# then misses the income filter for phrasings/typos it doesn't have keywords for.
# PAL's LLM planner understands the intent (typo-tolerant), computes deterministically
# (flow_type filter + date range), and phrases a friendly answer — falling back to
# legacy if it can't produce a valid plan.
_PAL_ELIGIBLE_ON_AGG = {
    "date_range_query", "revenue", "expenses", "cash_inflow", "cash_outflow",
}

# Income/expense signal words (substrings, so "earn" also catches earned/earning/
# earnings). English + a few common Sinhala stems. Liberal on purpose: a false
# positive only sends the query to PAL, which degrades to legacy if it can't plan.
_FINANCIAL_AGG_WORDS = (
    # income
    "earn", "income", "revenue", "sale", "turnover", "inflow", "receiv",
    "ආදාය", "ලැබ", "ඉපැය",
    # expense
    "spend", "spent", "expens", "expence", "cost", "expenditure", "outflow", "payable",
    "වියද", "ගෙවිය",
)


def _wants_financial_aggregation(normalized_q: str) -> bool:
    """True when the question is about money earned or spent — the case where PAL's
    neuro-symbolic path (LLM understanding + deterministic math) beats keyword rules."""
    return any(w in normalized_q for w in _FINANCIAL_AGG_WORDS)


# ── Conversational follow-up context (factual queries) ──────────────────────
# Note: the agentic engine (backend/agent/) gets multi-turn context natively
# from LangGraph's MessagesState/checkpointer. This still-default /ask-query
# path has no equivalent, so a lightweight LLM rewrite step fills the gap for
# follow-ups ("what about payables?") without touching the tested PAL/legacy
# routing below it -- it only rewrites the question text before routing.

# Markers suggesting the question leans on something said earlier in the chat
# rather than standing alone — a short question, or one starting with a
# connector/pronoun. Liberal on purpose: worst case we send a self-contained
# question through an LLM rewrite that returns it unchanged.
_FOLLOWUP_MARKERS = (
    "what about", "how about", "and ", "also", "same", "that", "those", "them", "it ",
    "further", "again", "instead",
    "ඒ ගැන", "තවත්", "ඒවා", "ඒක",
)


def _looks_like_followup(normalized_q: str) -> bool:
    if len(normalized_q.split()) <= 6:
        return True
    return any(m in normalized_q for m in _FOLLOWUP_MARKERS)


def _contextualize_question(question: str, conversation_history: list[dict] | None) -> str:
    """Rewrite a follow-up ("what about payables?") into a self-contained question
    using recent turns, so the existing keyword/PAL pipeline (unchanged) can route
    and answer it normally. No-ops (returns `question` unchanged) whenever there's
    no history or the question doesn't look like a follow-up — so callers that never
    pass conversation_history (all existing tests) are completely unaffected."""
    if not conversation_history:
        return question
    if not _looks_like_followup(dt.normalize_text(question)):
        return question

    history_text = "\n".join(
        f"Q: {t.get('question', '')}\nA: {t.get('answer', '')}"
        for t in conversation_history[-4:]
    )
    system = (
        "Rewrite the user's latest question into one fully self-contained question, "
        "using the conversation history only to resolve pronouns/short follow-up "
        "references (e.g. 'what about payables' after a receivables question -> "
        "'What is my total payable amount?'). If it's already self-contained, return "
        "it unchanged. Reply with ONLY the rewritten question, nothing else."
    )
    prompt = f"Conversation history:\n{history_text}\n\nLatest question: {question}\n\nRewritten question:"
    try:
        rewritten = call_llm(prompt, system=system).strip().strip('"').strip()
        return rewritten if rewritten else question
    except Exception:
        return question


def _legacy_answer(
    question: str,
    company_name: str,
    user_id: str,
    *,
    audit_extra: dict | None = None,
    require_provenance: bool = True,
) -> dict:
    analysis_result = dt.analyze_financial_query(question=question, company_name=company_name, user_id=user_id)
    evidence = analysis_result.get("evidence", [])

    # FR-22: Only answer when provenance is available for arithmetic/reasoning queries.
    # Listing intents (invoice_list etc.) have a valid "no results found" answer that is
    # NOT a hallucination — they simply report an empty set.  require_provenance=False is
    # set by the _NON_ARITHMETIC_INTENTS path for exactly this reason.
    if require_provenance and not evidence:
        refusal = (
            "I could not find any documents related to your query for the given company. "
            "Please upload relevant invoices or purchase orders first, or try a different company name."
        )
        return {
            "success": False,
            "direct_answer": refusal, "short_answer": refusal, "full_answer": refusal,
            "explanation": refusal, "evidence": [], "metrics": analysis_result.get("metrics", {}),
            "source_file": analysis_result.get("source_file", SOURCE),
            "computed": None, "citations": [],
            "audit": {"engine": "legacy_ad_hoc", "validation": "refused_no_provenance", **(audit_extra or {})},
        }

    answer_bundle = generate_explainable_answer(question=question, company_name=company_name, result=analysis_result)
    return {
        "success": analysis_result.get("success", False),
        "direct_answer": analysis_result.get("direct_answer") or answer_bundle.get("short_answer", ""),
        "short_answer": answer_bundle.get("short_answer", ""),
        "full_answer": answer_bundle.get("full_answer", analysis_result.get("explanation", "")),
        "explanation": answer_bundle.get("full_answer", analysis_result.get("explanation", "")),
        "evidence": evidence,
        "metrics": analysis_result.get("metrics", {}),
        "source_file": analysis_result.get("source_file", ""),
        "computed": None,
        "citations": [],
        "audit": {"engine": "legacy_ad_hoc", **(audit_extra or {})},
    }


def _empty_scope_answer(message: str) -> dict:
    return {
        "success": False, "direct_answer": message, "short_answer": message, "full_answer": message,
        "explanation": message, "evidence": [], "metrics": {}, "source_file": SOURCE,
        "computed": None, "citations": [], "audit": {"engine": "pal", "validation": "scope_empty"},
    }


def answer_financial_question(
    question: str,
    company_name: str,
    user_id: str,
    conversation_history: list[dict] | None = None,
) -> dict:
    # Apply Sinhala/typo normalization before routing (same as analyze_financial_query)
    _corrected, _ = dt.spell_correct_query(question)
    _normalized = dt.normalize_query(_corrected)

    # Advice-seeking questions ("how can I manage my budget") are a distinct class
    # from factual lookups/aggregations — answer them via the financial advisor,
    # grounded in the user's live data, instead of routing into PAL/legacy. A
    # plain reply to the advisor's own clarifying question (e.g. "my expenses
    # are rent 50000...") doesn't look advice-seeking on its own, so also stay
    # on this path when the previous turn was itself advisory.
    if dt._is_advice_question(_normalized) or continues_advisory_conversation(conversation_history):
        return generate_financial_advice(question, company_name, user_id, conversation_history)

    # Factual follow-ups ("what about payables?") get rewritten into a
    # self-contained question using recent turns, then re-routed through the
    # unchanged keyword/PAL pipeline below. No-ops when there's no history.
    contextualized = _contextualize_question(question, conversation_history)
    if contextualized != question:
        question = contextualized
        _corrected, _ = dt.spell_correct_query(question)
        _normalized = dt.normalize_query(_corrected)

    question_type = dt.route_question(_normalized)

    # Prefer PAL for income/expense aggregations (e.g. "how much did we earn this
    # month", typos included) — the LLM planner understands the intent robustly and
    # the executor computes it deterministically with the correct flow_type filter.
    _pal_override = (
        question_type in _PAL_ELIGIBLE_ON_AGG
        and _wants_financial_aggregation(_normalized)
    )

    if not _pal_override:
        if question_type in _NON_ARITHMETIC_INTENTS:
            # Listing queries — no provenance check (empty set is a valid answer)
            return _legacy_answer(question, company_name, user_id, require_provenance=False)

        if question_type in _LEGACY_DIRECT_INTENTS:
            # Deterministic aggregation — bypass PAL planner to avoid LLM flow_type errors
            return _legacy_answer(question, company_name, user_id, require_provenance=True)

    # Iteration 15 — FR-19: hybrid RAG + SQL scope; degrades silently to SQL-only
    documents_df, scope_error = resolve_scope_with_rag(question, company_name, user_id)
    if scope_error:
        return _empty_scope_answer(scope_error)

    plan = None
    error_reason = None
    attempts = []
    for attempt in range(MAX_RETRIES + 1):
        candidate_plan = plan_query(question, error_reason=error_reason)
        if candidate_plan is None:
            attempts.append({"attempt": attempt, "plan": None, "error": "planner_unavailable_or_unparseable"})
            break
        is_valid, error_reason = validate_plan(candidate_plan)
        attempts.append({"attempt": attempt, "plan": candidate_plan, "error": None if is_valid else error_reason})
        if is_valid:
            plan = candidate_plan
            break

    if plan is None:
        # PAL couldn't produce a valid plan within the retry budget -> degrade
        # to the legacy path rather than failing the user's query outright
        # (component-3.md failure table: degrade to best available).
        return _legacy_answer(question, company_name, user_id, audit_extra={"pal_attempts": attempts})

    rows = rows_for_plan(plan, documents_df)
    computed = execute_plan(plan, rows)

    if not computed.get("row_count"):
        # Validated plan, but nothing matched (component-3.md failure table:
        # "No rows retrieved -> broaden retrieval"). Simplified for this
        # iteration: degrade to the legacy path instead of re-querying with
        # widened filters -- still grounded in the same real data.
        return _legacy_answer(
            question, company_name, user_id,
            audit_extra={"pal_attempts": attempts, "pal_plan": plan, "reason": "no_rows_matched"},
        )

    answer_bundle = generate_pal_answer(question, company_name, plan, computed)

    used_doc_ids = {r["document_id"] for r in computed.get("rows_used", [])}
    evidence_df = documents_df[documents_df["document_id"].isin(used_doc_ids)]
    reason = f"PAL plan: {plan.get('task')} over canonical fields, validated against the allow-list."
    evidence = dt.build_evidence(evidence_df, reason)

    return {
        "success": True,
        "direct_answer": answer_bundle["short_answer"],
        "short_answer": answer_bundle["short_answer"],
        "full_answer": answer_bundle["full_answer"],
        "explanation": answer_bundle["full_answer"],
        "evidence": evidence,
        "metrics": {
            "company_name": company_name,
            "task": plan.get("task"),
            "computed_value": computed.get("value"),
            "currency": computed.get("currency"),
            "row_count": computed.get("row_count"),
        },
        "source_file": SOURCE,
        "computed": computed,
        "citations": [],  # bbox citations land once C1/C2 are wired into the live pipeline
        "audit": {"engine": "pal", "plan": plan, "validation": "passed", "attempts": len(attempts)},
    }
