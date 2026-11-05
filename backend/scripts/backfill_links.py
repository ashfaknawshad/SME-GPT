"""Backfill PO -> DN -> Invoice -> Receipt links for existing documents.

Dry run by default (reads only):
    python scripts/backfill_links.py
Apply (fills docNumber/poRef/dnRef/invoiceRef where empty, writes links and
forward-only status updates):
    python scripts/backfill_links.py --apply
Limit to one account:
    python scripts/backfill_links.py --email someone@example.com [--apply]

References come from the stored OCR text and legacy order_id (no LLM calls).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(_backend / ".env")

from dataset_manager import load_all_records  # noqa: E402
from db import get_conn  # noqa: E402
from references import derive_references  # noqa: E402
from order_reconciliation import reconcile_tenant  # noqa: E402

_REF_COLS = {"doc_number": "docNumber", "po_ref": "poRef", "dn_ref": "dnRef", "invoice_ref": "invoiceRef"}


def _tenants(email: str | None) -> list[tuple[str, str]]:
    with get_conn() as conn:
        cur = conn.cursor()
        if email:
            cur.execute('SELECT id, email FROM "User" WHERE email = %s', (email,))
        else:
            cur.execute(
                'SELECT DISTINCT d."tenantId" AS id, COALESCE(u.email, d."tenantId") AS email'
                ' FROM "FinancialDocument" d LEFT JOIN "User" u ON u.id = d."tenantId"'
                ' WHERE d."deletedAt" IS NULL'
            )
        return [(r["id"], r["email"]) for r in cur.fetchall()]


def _fill_refs(user_id: str, records: list[dict], apply: bool) -> list[str]:
    filled = []
    for rec in records:
        refs = derive_references(rec)
        todo = {k: v for k, v in refs.items() if v and not str(rec.get(k) or "").strip() or
                (v and str(rec.get(k) or "").strip().upper() in ("NULL", "NONE"))}
        if not todo:
            continue
        filled.append(f"{rec['document_id']}: " + ", ".join(f"{k}={v}" for k, v in todo.items()))
        if apply:
            sets = ", ".join(f'"{_REF_COLS[k]}" = %s' for k in todo)
            with get_conn() as conn:
                conn.cursor().execute(
                    f'UPDATE "FinancialDocument" SET {sets} WHERE "tenantId" = %s AND "documentId" = %s',
                    list(todo.values()) + [user_id, rec["document_id"]],
                )
    return filled


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--email", help="only this account")
    args = ap.parse_args()

    print(f"{'APPLYING' if args.apply else 'DRY RUN (nothing is written)'}\n")
    for user_id, email in _tenants(args.email):
        records = load_all_records(user_id=user_id)
        if not records:
            continue
        print(f"=== {email}  ({len(records)} documents)")
        for line in _fill_refs(user_id, records, args.apply):
            print(f"  ref  {line}")
        report = reconcile_tenant(user_id, dry_run=not args.apply)
        for l in sorted(report["links"]["links"], key=lambda x: (x["status"], x["from"])):
            print(f"  link {l['status']:9s} {l['from']:>6s} -> {l['to']:<6s} {l['link_type']:15s} "
                  f"{l['confidence']:.2f}  ({'; '.join(l['reasons'])})")
        for o in report["orders"]:
            print(f"  order {' + '.join(o['documents'])}: {o['status']}")
            for msg in o["issues"]:
                print(f"     ! {msg}")
            for u in o["updates"]:
                print(f"     ~ {u['document_id']}.{u['field']}: {u['from'] or '-'} -> {u['to']}")
        print()


if __name__ == "__main__":
    main()
