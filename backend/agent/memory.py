"""Conversation memory for the agentic query engine (Phase 1).

Prefers a Postgres-backed checkpointer -- persists across backend restarts,
consistent with the rest of the app's Postgres-first storage -- and falls
back to an in-process MemorySaver if DATABASE_URL is unset or Postgres is
unreachable, matching the app's existing graceful-degradation posture (see
CLAUDE.md "Architecture Invariants"). Uses its own small connection pool
(autocommit=True, as langgraph-checkpoint-postgres requires) rather than
db.py's pool, which is tuned for the app's own transactional access pattern.
"""
from __future__ import annotations

import os

from langgraph.checkpoint.memory import MemorySaver

_checkpointer = None


def get_checkpointer():
    """Returns a process-wide singleton checkpointer, initialized on first use."""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
            from langgraph.checkpoint.postgres import PostgresSaver

            pool = ConnectionPool(
                conninfo=database_url,
                min_size=1,
                max_size=3,
                kwargs={
                    "autocommit": True,
                    "row_factory": dict_row,
                    "prepare_threshold": None,
                    "connect_timeout": 10,
                    # Detect silently-dropped Supabase pooler connections fast.
                    "keepalives": 1,
                    "keepalives_idle": 15,
                    "keepalives_interval": 5,
                    "keepalives_count": 3,
                },
                open=True,
                # Supabase's PgBouncer closes idle connections; without a
                # liveness probe on checkout, the first /chat after an idle
                # period got a dead connection and 500'd (same fix as db.py).
                check=ConnectionPool.check_connection,
                max_idle=float(os.getenv("DB_POOL_MAX_IDLE_SECS", "120")),
            )
            saver = PostgresSaver(pool)
            saver.setup()
            _checkpointer = saver
            print("[Agent] Postgres checkpointer ready.", flush=True)
            return _checkpointer
        except Exception as exc:
            print(f"[Agent] Postgres checkpointer unavailable ({exc}); using in-memory.", flush=True)

    _checkpointer = MemorySaver()
    return _checkpointer


def reset_checkpointer_for_tests() -> None:
    """Test-only escape hatch so each test gets an isolated MemorySaver instead
    of sharing the process-wide singleton."""
    global _checkpointer
    _checkpointer = None
