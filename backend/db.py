"""PostgreSQL (Supabase) connection helper — pool-backed.

Uses psycopg_pool.ConnectionPool so every request reuses an already-open
connection instead of paying a 200-400 ms TCP+SSL handshake on each call.

Pool settings (both pools use dict_row so callers get named columns):
  min_size=1  — keep 1 connection warm at idle
  max_size=8  — stays within Supabase free tier limit (~60 concurrent)
  prepare_threshold=None — required for PgBouncer transaction mode (port 6543)

All callers continue to use get_conn() as a context manager:

    with get_conn() as conn:
        cur = conn.cursor()
        ...  # rows are dicts: row["columnName"]
"""

import os
import uuid
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

# Load backend/.env regardless of the process working directory.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to backend/.env "
            "(use Supabase pooler URL port 6543 for best performance)."
        )
    return url


# ---------------------------------------------------------------------------
# Connection pool — created once at import time, reused for every request.
# Falls back to None if DATABASE_URL is absent (unit-test environments).
# ---------------------------------------------------------------------------
_pool = None

def _init_pool():
    global _pool
    url = os.getenv("DATABASE_URL")
    if not url:
        return
    try:
        from psycopg_pool import ConnectionPool
        # Env-tunable so the pool can grow when you move off the Supabase free
        # tier (or run more backend instances) without a code change. Defaults
        # keep min=1/max=8 — safe within the free pooler's ~60 connection cap
        # even across a few instances (instances × DB_POOL_MAX must stay < cap).
        _min = int(os.getenv("DB_POOL_MIN", "1"))
        _max = int(os.getenv("DB_POOL_MAX", "8"))
        _pool = ConnectionPool(
            conninfo=url,
            min_size=_min,
            max_size=max(_min, _max),
            kwargs={
                "row_factory": dict_row,
                "prepare_threshold": None,
                # Bound new-connection time so a dead pooler doesn't hang a request.
                "connect_timeout": 10,
                # TCP keepalives — let the OS detect a silently-dropped Supabase
                # pooler connection in ~20s instead of blocking until the 60s+
                # app/socket timeout. Pairs with run_with_retry for fast recovery.
                "keepalives": 1,
                "keepalives_idle": 15,
                "keepalives_interval": 5,
                "keepalives_count": 3,
            },
            open=True,
            # Supabase's PgBouncer closes idle connections, so a pooled
            # connection can be silently dead by the next request. check_connection
            # runs a cheap liveness probe on checkout and recycles dead ones,
            # preventing "connection [BAD]" 500s on the second/follow-up query.
            check=ConnectionPool.check_connection,
            # Recycle connections that have been idle longer than the pooler's
            # own idle timeout, so we rarely hand out a stale one in the first place.
            max_idle=float(os.getenv("DB_POOL_MAX_IDLE_SECS", "120")),
            reconnect_failed=lambda p: print(
                "[DB] Pool reconnect failed — will retry on next request.", flush=True
            ),
        )
        print("[DB] Connection pool ready (min=1, max=8, health-checked).", flush=True)
    except ImportError:
        # psycopg_pool not installed — fall back to per-request connections
        print("[DB] psycopg_pool not available, using per-request connections.", flush=True)
    except Exception as exc:
        print(f"[DB] Pool init failed ({exc}), using per-request connections.", flush=True)

_init_pool()


# Errors that mean "the connection died" rather than "the query was wrong".
# These are safe to retry on a fresh connection.
_RETRYABLE_DB_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)


def run_with_retry(fn, *, attempts: int = 2):
    """Run a read operation, retrying once if the pooled connection was aborted.

    The connection health-check (check_connection) probes liveness at *checkout*,
    but Supabase's PgBouncer / the network can still abort a connection in the
    window between the probe and the actual query (seen as SSL SYSCALL error /
    WSAECONNABORTED 10053). The pool discards the dead connection automatically,
    so simply re-running the operation gets a fresh, healthy one.

    `fn` MUST open its own `get_conn()` block (so the retry uses a new connection)
    and MUST be idempotent — use only for reads, not for writes that may have
    partially committed.
    """
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except _RETRYABLE_DB_ERRORS as exc:
            last_exc = exc
            print(
                f"[DB] connection aborted ({exc.__class__.__name__}: {exc}); "
                f"retry {i + 1}/{attempts}",
                flush=True,
            )
    raise last_exc


@contextmanager
def get_conn():
    """Yield a dict-row connection (commits on success, rolls back on error).

    Uses the connection pool when available; falls back to a direct
    psycopg.connect() in environments where the pool is unavailable.
    """
    if _pool is not None:
        with _pool.connection() as conn:
            yield conn
    else:
        conn = psycopg.connect(
            get_database_url(), row_factory=dict_row, prepare_threshold=None
        )
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def new_id(prefix: str) -> str:
    """Generate a primary-key string (Prisma's cuid() default is client-side,
    so raw inserts must supply their own id)."""
    return f"{prefix}_{uuid.uuid4().hex}"
