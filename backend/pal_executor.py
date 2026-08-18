"""Component 3 — Deterministic executor (pandas, no exec/eval).

Loads row_records[] into a DataFrame and applies a validated plan's filters
+ aggregation exactly (docs/components/component-3.md "Executor"). This is
the only place arithmetic actually happens -- the LLM never computes a
number, it only proposes a plan that this module either executes literally
or doesn't run at all (pal_validator.py rejects anything outside the
allow-list first).
"""
from __future__ import annotations

import pandas as pd

from correction_engine import _parse_date_flexible

_OPS = {
    "eq": lambda series, value: series == value,
    "in": lambda series, value: series.isin(value if isinstance(value, list) else [value]),
    "contains": lambda series, value: series.astype(str).str.contains(str(value), case=False, na=False),
    "gte": lambda series, value: pd.to_numeric(series, errors="coerce") >= value,
    "lte": lambda series, value: pd.to_numeric(series, errors="coerce") <= value,
    "between": lambda series, value: pd.to_numeric(series, errors="coerce").between(value[0], value[1]),
}

# doc_date is the only date-typed canonical field (pal_validator.CANONICAL_FIELDS).
# Its underlying values come straight from the extracted `date` column, which OCR
# produces in wildly inconsistent formats ("16 Apr 2026", "8/4/2022", "29-06-2026",
# "Nov 18, 2020" all coexist in real data) -- numeric coercion (the default _OPS
# path) turns every one of them into NaN and then crashes comparing NaN against the
# ISO date-string bounds the planner emits. Reuse the same ISO-first/dayfirst-
# fallback parser already used for due_date/delivery_date (correction_engine.py) so
# doc_date filtering agrees with the rest of the app's date semantics.
_DATE_FIELDS = {"doc_date"}
_DATE_OPS = {"gte", "lte", "between"}


def _date_op(series: pd.Series, op: str, value) -> pd.Series:
    parsed = series.map(lambda v: _parse_date_flexible(str(v)) if v not in (None, "NULL", "") else None)
    if op == "between":
        lo, hi = _parse_date_flexible(str(value[0])), _parse_date_flexible(str(value[1]))
        return parsed.map(lambda d: d is not None and lo is not None and hi is not None and lo <= d <= hi)
    bound = _parse_date_flexible(str(value))
    if op == "gte":
        return parsed.map(lambda d: d is not None and bound is not None and d >= bound)
    return parsed.map(lambda d: d is not None and bound is not None and d <= bound)  # lte

_AGGS = {
    "sum": lambda s: float(s.sum()) if len(s) else 0.0,
    "avg": lambda s: float(s.mean()) if len(s) else 0.0,
    "count": lambda s: int(s.count()),
    "max": lambda s: float(s.max()) if len(s) else 0.0,
    "min": lambda s: float(s.min()) if len(s) else 0.0,
}


def _apply_filters(df: pd.DataFrame, filters: list[dict] | None) -> pd.DataFrame:
    for f in filters or []:
        field, op, value = f["field"], f["op"], f.get("value")
        if field not in df.columns:
            return df.iloc[0:0]
        if field in _DATE_FIELDS and op in _DATE_OPS:
            mask = _date_op(df[field], op, value)
        else:
            mask = _OPS[op](df[field], value)
        df = df[mask.fillna(False)]
    return df


def _rows_used(df: pd.DataFrame) -> list[dict]:
    return [
        {"document_id": r.get("document_id"), "line_no": r.get("line_no")}
        for r in df.to_dict(orient="records")
    ]


def _numeric_series(df: pd.DataFrame, field: str | None):
    if not field or field not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[field], errors="coerce").dropna()


def _currency_of(df: pd.DataFrame) -> str | None:
    if df.empty or "currency" not in df.columns:
        return None
    currencies = sorted({str(c) for c in df["currency"].dropna().unique()})
    if len(currencies) == 1:
        return currencies[0]
    return "mixed" if currencies else None


def _per_currency_breakdown(df: pd.DataFrame, field: str, agg: str) -> list[dict]:
    breakdown = []
    for currency, group in df.groupby("currency"):
        series = _numeric_series(group, field) if agg != "count" else group.get(field, pd.Series(dtype=object))
        value = _AGGS[agg](series)
        breakdown.append({"currency": currency, "value": value})
    return breakdown


def execute_plan(plan: dict, rows: list[dict]) -> dict:
    """Returns a dict always containing `value`, `currency`, `operation`,
    `rows_used`, `row_count` -- plus task-specific extras (`groups` for
    group_by_sum, `difference` for compare, `per_currency` when a single
    aggregate spans more than one currency)."""
    df = pd.DataFrame(rows)
    task = plan["task"]
    measure = plan.get("measure") or {}
    field = measure.get("field")

    if df.empty:
        return {"value": None, "currency": None, "operation": task, "rows_used": [], "row_count": 0}

    if task == "compare":
        filter_sets = plan.get("compare_filters") or [[], []]
        agg_name = measure.get("agg") or "sum"
        results = []
        all_used = pd.DataFrame()
        for filter_set in filter_sets:
            sub = _apply_filters(df, filter_set)
            all_used = pd.concat([all_used, sub])
            series = _numeric_series(sub, field) if agg_name != "count" else sub.get(field, pd.Series(dtype=object))
            results.append({"value": _AGGS[agg_name](series), "row_count": len(sub), "filters": filter_set})
        difference = None
        if all(isinstance(r["value"], (int, float)) for r in results):
            difference = round(results[0]["value"] - results[1]["value"], 2)
        return {
            "value": results, "currency": _currency_of(all_used), "operation": f"compare({agg_name}({field}))",
            "difference": difference, "rows_used": _rows_used(all_used), "row_count": len(all_used),
        }

    filtered = _apply_filters(df, plan.get("filters"))
    currency = _currency_of(filtered)

    if task in ("aggregate_sum", "aggregate_avg", "aggregate_count"):
        # measure.agg is the source of truth (already validated against
        # AGGREGATIONS) -- the task name is a category label, not binding,
        # so a plan can e.g. ask for "aggregate_sum" with agg="max".
        agg = measure.get("agg") or {"aggregate_sum": "sum", "aggregate_avg": "avg", "aggregate_count": "count"}[task]
        series = _numeric_series(filtered, field) if agg != "count" else filtered.get(field, pd.Series(dtype=object))
        value = _AGGS[agg](series)
        result = {
            "value": value, "currency": currency, "operation": f"{agg}({field})",
            "rows_used": _rows_used(filtered), "row_count": len(filtered),
        }
        if currency == "mixed" and agg != "count":
            result["per_currency"] = _per_currency_breakdown(filtered, field, agg)
        return result

    if task == "group_by_sum":
        group_fields = plan.get("group_by") or []
        if field not in filtered.columns:
            return {"value": None, "currency": currency, "operation": f"group_by_sum({field})",
                    "rows_used": [], "row_count": 0, "groups": []}
        grouped = filtered.groupby(group_fields, dropna=False).apply(
            lambda g: _AGGS["sum"](_numeric_series(g, field)), include_groups=False
        ).reset_index(name=field)
        # Also return the overall total across every group. A "which unpaid /
        # break this down by vendor" answer usually leads with the headline
        # figure (sum of the groups); without emitting it here that number is a
        # multi-term sum the LLM would have to add up itself, which the grounding
        # guard then flags as unverified and blanks the whole answer. Computing
        # it here keeps it a real tool-produced number -- still pure pandas, the
        # LLM never does the arithmetic.
        overall = _AGGS["sum"](_numeric_series(filtered, field))
        result = {
            "value": overall, "currency": currency, "operation": f"group_by_sum({field})",
            "rows_used": _rows_used(filtered), "row_count": len(filtered),
            "groups": grouped.to_dict(orient="records"),
        }
        if currency == "mixed":
            # Groups may span currencies -- a single scalar total would conflate
            # them, so surface the per-currency split the same way aggregate_sum
            # does (and both remain grounded).
            result["per_currency"] = _per_currency_breakdown(filtered, field, "sum")
        return result

    if task == "lookup_value":
        matches = filtered.head(5)
        values = matches[field].tolist() if field in matches.columns else []
        return {
            "value": values, "currency": currency, "operation": f"lookup({field})",
            "rows_used": _rows_used(matches), "row_count": len(matches),
        }

    return {"value": None, "currency": currency, "operation": task, "rows_used": [], "row_count": 0}
