"""Component 3 — Plan validator (symbolic guard).

Rejects a PAL plan (from pal_planner.py) if it falls outside the allow-list:
unknown task, unsupported filter operator, non-canonical field, or an
aggregate/group-by/compare task missing its measure field
(docs/components/component-3.md "Validator"). Deterministic, no LLM involved
-- this is what makes PAL safe to wire into the live /ask-query endpoint: a
plan that doesn't pass here never reaches the executor.
"""
from __future__ import annotations

TASKS = {"aggregate_sum", "aggregate_avg", "aggregate_count", "compare", "lookup_value", "group_by_sum"}
FILTER_OPS = {"eq", "in", "contains", "gte", "lte", "between"}
AGGREGATIONS = {"sum", "avg", "count", "max", "min"}

# component-3.md's 10 canonical fields, plus `flow_type`, `paid_status`, and
# `received_status` -- deliberate, documented extensions. Payable/receivable/
# expense/income classification is core to every financial query this app
# answers and there's no other canonical field to express it. `paid_status`/
# `received_status` were added because "how much do we owe"/"how much are we
# owed" queries need to exclude already-settled amounts to answer correctly --
# without them, aggregate_financials/PAL could only sum ALL payable/receivable
# activity, settled or not (mirrors what data_tools.analyze_financial_query's
# legacy "payable"/"receivable" handlers already do -- see agent/tools.py's
# aggregate_financials docstring for the query-time guidance this enables).
# Company-level scoping is handled before the plan ever runs (pal_scope.py),
# so `company_name` doesn't need to be here.
CANONICAL_FIELDS = {
    "item", "description", "qty", "unit_price", "total", "tax", "discount",
    "currency", "doc_date", "vendor", "flow_type", "paid_status", "received_status",
    "document_type",
}

_NEEDS_MEASURE = {"aggregate_sum", "aggregate_avg", "aggregate_count", "group_by_sum", "compare"}


def _validate_filters(filters) -> str | None:
    for f in filters or []:
        if not isinstance(f, dict):
            return "filter_is_not_an_object"
        if f.get("field") not in CANONICAL_FIELDS:
            return f"field_not_canonical: {f.get('field')!r}"
        if f.get("op") not in FILTER_OPS:
            return f"operator_not_in_allowlist: {f.get('op')!r}"
    return None


def validate_plan(plan: dict) -> tuple[bool, str]:
    """Returns (is_valid, error_reason). error_reason is "" when valid."""
    if not isinstance(plan, dict):
        return False, "plan_is_not_an_object"

    task = plan.get("task")
    if task not in TASKS:
        return False, f"task_not_in_allowlist: {task!r}"

    measure = plan.get("measure") or {}
    if task in _NEEDS_MEASURE:
        measure_field = measure.get("field")
        if not measure_field:
            return False, "ambiguous_target_field: measure.field is required"
        if measure_field not in CANONICAL_FIELDS:
            return False, f"field_not_canonical: {measure_field!r}"
        if task != "compare":
            agg = measure.get("agg")
            if agg not in AGGREGATIONS:
                return False, f"aggregation_not_in_allowlist: {agg!r}"

    if task == "compare":
        compare_filters = plan.get("compare_filters")
        if not isinstance(compare_filters, list) or len(compare_filters) != 2:
            return False, "compare_requires_exactly_two_filter_sets"
        for filter_set in compare_filters:
            error = _validate_filters(filter_set)
            if error:
                return False, error
    else:
        error = _validate_filters(plan.get("filters"))
        if error:
            return False, error

    for group_field in plan.get("group_by") or []:
        if group_field not in CANONICAL_FIELDS:
            return False, f"field_not_canonical: {group_field!r}"

    if task == "group_by_sum" and not plan.get("group_by"):
        return False, "group_by_sum_requires_a_non_empty_group_by"

    if task == "lookup_value" and not plan.get("filters"):
        return False, "lookup_value_requires_at_least_one_filter"

    return True, ""
