"""
Order reconciliation backfill — recomputes and persists every PO's status from
its Delivery Notes (see backend/order_reconciliation.py).

Run after deploying a change to the reconciliation rules, or to repair POs
saved while reconciliation wasn't running. Idempotent: a second run changes
nothing.

Usage (from the backend directory):
    python scripts/reconcile_orders.py            # apply
    python scripts/reconcile_orders.py --dry-run  # only report
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

_backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend_dir))
load_dotenv(_backend_dir / ".env")

import order_reconciliation as orec  # noqa: E402  (needs the path + env above)


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        # Report what would change without writing.
        import dataset_manager
        dataset_manager.update_record_for_user = lambda *a, **k: None

    summaries = orec.reconcile_all_orders()
    changed = [s for s in summaries if s["changes"]]
    for s in changed:
        for c in s["changes"]:
            print(f"{s['order_id']}: {c['document_id']} {c['from'] or '-'} -> {c['to']}")
    verb = "would change" if dry_run else "changed"
    print(f"{len(summaries)} orders checked, {sum(len(s['changes']) for s in changed)} PO status(es) {verb}.")


if __name__ == "__main__":
    main()
