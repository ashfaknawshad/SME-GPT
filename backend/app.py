import asyncio
import os
import json
import shutil
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv()  # must run before any local module is imported so env vars are set

import jwt
import psycopg
from fastapi import Body, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from document_pipeline import process_uploaded_document, OCREngineUnavailableError
from dataset_manager import (
    upsert_confirmed_record,
    save_input_json,
    find_duplicate_record,
    load_all_records,
    count_records,
    load_records,
    get_record_by_id_for_user,
    update_record_for_user,
    delete_record_for_user,
    invalidate_column_cache,
)
from pal_qa import answer_financial_question

JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET or JWT_SECRET == "your_super_secret_key_123":
    raise RuntimeError(
        "JWT_SECRET must be set to a strong random secret in backend/.env. "
        "The app will not start with an empty or default secret."
    )
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
DATABASE_URL = os.getenv("DATABASE_URL", "")

app = FastAPI(title="SME-GPT Financial Document Backend")

# Always allow local dev origins; extend with CORS_ALLOW_ORIGINS env var for production
_ALWAYS_ALLOW = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://192.168.56.1:3000",
]
_env_extra = [
    o.strip()
    for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
    if o.strip()
]
CORS_ORIGINS = list(dict.fromkeys(_ALWAYS_ALLOW + _env_extra))  # dedup, preserve order

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    # TEMPORARY — allow cloudflared tunnels for phone testing. Remove when done.
    allow_origin_regex=r"https://.*\.trycloudflare\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Phase 1: agentic conversational query engine (backend/agent/), feature-flagged
# alongside the existing PAL/legacy engine behind /ask-query. Off by default —
# /chat returns 503 until explicitly enabled. See backend/agent/service.py.
AGENT_QUERY_ENGINE_ENABLED = os.getenv("AGENT_QUERY_ENGINE_ENABLED", "false").strip().lower() == "true"

# ── Iteration 8: in-process rate limiter ────────────────────────────────────
# Sliding-window counter per IP. Configure via environment variables.
_RATE_WINDOW    = int(os.getenv("RATE_LIMIT_WINDOW_SECS",    "60"))
_RATE_ASK_QUERY = int(os.getenv("RATE_LIMIT_ASK_QUERY",      "30"))
_RATE_PROCESS   = int(os.getenv("RATE_LIMIT_PROCESS_DOC",    "10"))
_RATE_DEFAULT   = int(os.getenv("RATE_LIMIT_DEFAULT",        "120"))
_RATE_LIMITS: Dict[str, int] = {
    "/ask-query":               _RATE_ASK_QUERY,
    "/chat":                    _RATE_ASK_QUERY,
    "/process-document":        _RATE_PROCESS,
    "/process-document-stream": _RATE_PROCESS,
}
_rate_buckets: Dict[str, list] = defaultdict(list)  # key → [timestamps]
_rate_lock = threading.Lock()


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    path = request.url.path
    limit = next(
        (v for k, v in _RATE_LIMITS.items() if path.startswith(k)),
        _RATE_DEFAULT,
    )
    now = time.time()
    bucket_key = f"{client_ip}:{path}"

    with _rate_lock:
        # drop entries older than the window
        fresh = [t for t in _rate_buckets[bucket_key] if now - t < _RATE_WINDOW]
        # evict the key entirely when it has no in-window entries (prevents unbounded key growth)
        if not fresh:
            _rate_buckets.pop(bucket_key, None)
        if len(fresh) >= limit:
            return JSONResponse(
                {"detail": "Rate limit exceeded. Please slow down."},
                status_code=429,
            )
        fresh.append(now)
        _rate_buckets[bucket_key] = fresh

    return await call_next(request)
# ─────────────────────────────────────────────────────────────────────────────

_SESSION_TTL_SECS = int(os.getenv("SESSION_TTL_SECS", str(60 * 60 * 2)))  # default 2 hours


def save_processing_session(session_id: str, user_id: str, data: dict):
    """Persist an in-progress /process-document extraction so a backend
    restart before /confirm-save doesn't lose the user's upload."""
    query = """
    INSERT INTO processing_sessions (id, user_id, data)
    VALUES (%s, %s, %s::jsonb)
    ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, user_id = EXCLUDED.user_id
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (session_id, str(user_id), json.dumps(data, ensure_ascii=False)))
        conn.commit()


def load_processing_session(session_id: str) -> dict | None:
    query = "SELECT data FROM processing_sessions WHERE id = %s"
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (session_id,))
            row = cur.fetchone()
    if not row:
        return None
    data = row.get("data") if isinstance(row, dict) else row[0]
    return data


def delete_processing_session(session_id: str):
    query = "DELETE FROM processing_sessions WHERE id = %s"
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (session_id,))
        conn.commit()


def _evict_stale_sessions():
    while True:
        time.sleep(300)  # check every 5 minutes
        try:
            query = "DELETE FROM processing_sessions WHERE created_at < now() - interval '%s seconds'"
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, (_SESSION_TTL_SECS,))
                    evicted = cur.rowcount
                conn.commit()
            if evicted:
                print(f"[SESSION GC] evicted {evicted} stale sessions", flush=True)
        except Exception as exc:
            print(f"[SESSION GC] eviction pass failed: {exc}", flush=True)


# NOTE (FR-31): image files in saved_documents/ are NOT application-layer encrypted.
# In production, place this directory on an encrypted volume (e.g., EBS/LUKS encryption).
SAVED_DOCS_DIR = Path("saved_documents")
SAVED_DOCS_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/saved-documents", StaticFiles(directory=str(SAVED_DOCS_DIR)), name="saved-documents")


# ── Iteration 13: standardised error responses (GAP-I) ──────────────────────
class ErrorResponse(BaseModel):
    success: bool = False
    error_code: str
    message: str


_ERROR_CODE_BY_STATUS = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "DOCUMENT_NOT_FOUND",
    409: "CONFLICT",
    429: "RATE_LIMITED",
    503: "SERVICE_UNAVAILABLE",
}


def _cors_headers(request: Request) -> dict:
    """Echo CORS headers onto error responses.

    Exception handlers run outside CORSMiddleware, so without this an error
    response reaches the browser with no Access-Control-Allow-Origin header and
    the fetch fails with an opaque "failed to fetch" instead of the real error.
    """
    origin = request.headers.get("origin")
    if origin and origin in CORS_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    error_code = _ERROR_CODE_BY_STATUS.get(exc.status_code, f"HTTP_{exc.status_code}")
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error_code=error_code, message=str(exc.detail)).model_dump(),
        headers=_cors_headers(request),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Full traceback server-side only -- the response body must never leak
    # exception text (could contain SQL, file paths, or other internals).
    import traceback
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="An unexpected error occurred.",
        ).model_dump(),
        headers=_cors_headers(request),
    )
# ─────────────────────────────────────────────────────────────────────────────


# =========================
# DB HELPERS
# =========================
@contextmanager
def get_db_connection():
    """Yield a pooled dict-row connection (commits on success, rolls back on error).

    Delegates to db.get_conn() so all 20 call sites share one connection pool —
    no new TCP+SSL handshake per API call once the pool is warm.
    Note: rows are now dicts (use row["column"] not row[0]).
    """
    from db import get_conn as _pool_get_conn
    with _pool_get_conn() as conn:
        yield conn


def ensure_query_history_table():
    if not DATABASE_URL:
        return

    query = """
    CREATE TABLE IF NOT EXISTS query_history (
        id UUID PRIMARY KEY,
        user_id TEXT NOT NULL,
        company_name TEXT,
        question TEXT NOT NULL,
        answer TEXT,
        explanation TEXT,
        metrics JSONB,
        evidence JSONB,
        source_file TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
        conn.commit()


def ensure_processing_sessions_table():
    if not DATABASE_URL:
        return

    query = """
    CREATE TABLE IF NOT EXISTS processing_sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        data JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
        conn.commit()


def _scan_and_create_alerts():
    """Daily job: check every user's financial snapshot for alert-worthy
    conditions (alerts.py) and persist any newly-fired ones. Best-effort per
    user -- one user's failure doesn't stop the scan for the rest."""
    from alerts import check_alerts_for_user
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id, "companyName" FROM "User"')
                users = cur.fetchall()
    except Exception as e:
        print(f"[ALERTS] failed to list users: {e}", flush=True)
        return

    total_created = 0
    for u in users:
        user_id = u.get("id") if isinstance(u, dict) else u[0]
        company_name = (u.get("companyName") if isinstance(u, dict) else u[1]) or ""
        try:
            created = check_alerts_for_user(user_id, company_name)
            total_created += len(created)
        except Exception as e:
            print(f"[ALERTS] scan failed for user {user_id}: {e}", flush=True)
    print(f"[ALERTS] scan complete — {total_created} new alert(s) across {len(users)} user(s)", flush=True)


@app.on_event("startup")
def startup_event():
    try:
        ensure_query_history_table()
        print("[DB] Query history table ready", flush=True)
    except Exception as e:
        print(f"[DB] Query history table init skipped/failed: {e}", flush=True)
    try:
        ensure_processing_sessions_table()
        print("[DB] Processing sessions table ready", flush=True)
    except Exception as e:
        print(f"[DB] Processing sessions table init skipped/failed: {e}", flush=True)
    threading.Thread(target=_evict_stale_sessions, daemon=True, name="session-gc").start()
    print("[SESSION GC] stale session eviction thread started", flush=True)
    invalidate_column_cache()

    # Proactive financial alerts: daily scan for negative/declining cash flow (see alerts.py)
    if os.getenv("ALERTS_ENABLED", "true").lower() == "true":
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            _sched = BackgroundScheduler(daemon=True)
            _sched.add_job(_scan_and_create_alerts, CronTrigger(hour=7, minute=0))
            _sched.start()
            print("[SCHEDULER] Financial alert scanner started", flush=True)
        except Exception as e:
            print(f"[SCHEDULER] Failed to start scheduler: {e}", flush=True)


def save_query_history_to_db(
    user_id: str,
    company_name: str,
    question: str,
    answer: str,
    explanation: str,
    metrics: dict,
    evidence: list,
    source_file: str,
    conversation_id: str | None = None,
):
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")

    history_id = str(uuid.uuid4())

    query = """
    INSERT INTO query_history (
        id, user_id, company_name, question, answer, explanation, metrics, evidence, source_file, conversation_id
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (
                    history_id,
                    str(user_id),
                    company_name,
                    question,
                    answer,
                    explanation,
                    json.dumps(metrics, ensure_ascii=False),
                    json.dumps(evidence, ensure_ascii=False),
                    source_file,
                    str(conversation_id) if conversation_id else None,
                ),
            )
        conn.commit()

    return history_id


def load_conversation_turns(user_id: str, conversation_id: str, limit: int = 8):
    """Last `limit` turns of a conversation, oldest-first, for feeding LLM context."""
    if not DATABASE_URL:
        return []

    query = """
    SELECT question, answer, explanation, created_at
    FROM query_history
    WHERE user_id = %s AND conversation_id = %s
    ORDER BY created_at DESC
    LIMIT %s
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (str(user_id), str(conversation_id), limit))
            rows = cur.fetchall()

    cols = ["question", "answer", "explanation", "created_at"]
    turns = []
    for row in rows:
        r = row if isinstance(row, dict) else dict(zip(cols, row))
        turns.append({
            "question": r["question"] or "",
            "answer": r["answer"] or "",
            "explanation": r["explanation"] or "",
        })
    turns.reverse()  # oldest-first
    return turns


def load_conversations_for_user(user_id: str, limit: int = 30):
    """One row per conversation_id: first question as title, last activity, turn count."""
    if not DATABASE_URL:
        return []

    query = """
    SELECT
        conversation_id,
        (ARRAY_AGG(question ORDER BY created_at ASC))[1]  AS title,
        MAX(created_at)                                   AS last_activity,
        COUNT(*)                                           AS turn_count
    FROM query_history
    WHERE user_id = %s AND conversation_id IS NOT NULL
    GROUP BY conversation_id
    ORDER BY MAX(created_at) DESC
    LIMIT %s
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (str(user_id), limit))
            rows = cur.fetchall()

    cols = ["conversation_id", "title", "last_activity", "turn_count"]
    conversations = []
    for row in rows:
        r = row if isinstance(row, dict) else dict(zip(cols, row))
        conversations.append({
            "conversation_id": str(r["conversation_id"]),
            "title": r["title"] or "",
            "last_activity": r["last_activity"].isoformat() if r["last_activity"] else "",
            "turn_count": r["turn_count"],
        })
    return conversations


def load_conversation_thread_for_user(user_id: str, conversation_id: str):
    """Full ordered turn list for one conversation — used to reopen a chat thread."""
    query = """
    SELECT id, company_name, question, answer, explanation, metrics, evidence, source_file, created_at
    FROM query_history
    WHERE user_id = %s AND conversation_id = %s
    ORDER BY created_at ASC
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (str(user_id), str(conversation_id)))
            rows = cur.fetchall()

    cols = ["id","company_name","question","answer","explanation","metrics","evidence","source_file","created_at"]
    turns = []
    for row in rows:
        r = row if isinstance(row, dict) else dict(zip(cols, row))
        turns.append({
            "id": str(r["id"]),
            "company_name": r["company_name"] or "",
            "question": r["question"] or "",
            "answer": r["answer"] or "",
            "explanation": r["explanation"] or "",
            "metrics": r["metrics"] or {},
            "evidence": r["evidence"] or [],
            "source_file": r["source_file"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else "",
        })
    return turns


def load_query_history_for_user(user_id: str):
    if not DATABASE_URL:
        return []

    query = """
    SELECT id, company_name, question, answer, explanation, metrics, evidence, source_file, created_at, conversation_id
    FROM query_history
    WHERE user_id = %s
    ORDER BY created_at DESC
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (str(user_id),))
            rows = cur.fetchall()

    cols = ["id","company_name","question","answer","explanation","metrics","evidence","source_file","created_at","conversation_id"]
    history = []
    for row in rows:
        r = row if isinstance(row, dict) else dict(zip(cols, row))
        history.append({
            "id": str(r["id"]),
            "company_name": r["company_name"] or "",
            "question": r["question"] or "",
            "answer": r["answer"] or "",
            "explanation": r["explanation"] or "",
            "metrics": r["metrics"] or {},
            "evidence": r["evidence"] or [],
            "source_file": r["source_file"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else "",
            "conversation_id": str(r["conversation_id"]) if r.get("conversation_id") else None,
        })
    return history


def get_query_history_item_for_user(user_id: str, history_id: str):
    query = """
    SELECT id, company_name, question, answer, explanation, metrics, evidence, source_file, created_at
    FROM query_history
    WHERE user_id = %s AND id = %s
    LIMIT 1
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (str(user_id), str(history_id)))
            row = cur.fetchone()

    if not row:
        return None

    cols = ["id","company_name","question","answer","explanation","metrics","evidence","source_file","created_at"]
    r = row if isinstance(row, dict) else dict(zip(cols, row))
    return {
        "id": str(r["id"]),
        "company_name": r["company_name"] or "",
        "question": r["question"] or "",
        "answer": r["answer"] or "",
        "explanation": r["explanation"] or "",
        "metrics": r["metrics"] or {},
        "evidence": r["evidence"] or [],
        "source_file": r["source_file"] or "",
        "created_at": r["created_at"].isoformat() if r["created_at"] else "",
    }


def delete_query_history_item_for_user(user_id: str, history_id: str):
    query = """
    DELETE FROM query_history
    WHERE user_id = %s AND id = %s
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (str(user_id), str(history_id)))
            deleted = cur.rowcount
        conn.commit()

    return deleted > 0


def clear_query_history_for_user(user_id: str):
    query = """
    DELETE FROM query_history
    WHERE user_id = %s
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (str(user_id),))
            deleted = cur.rowcount
        conn.commit()

    return deleted


# =========================
# AUTH HELPERS
# =========================
def _get_session_version(user_id: str) -> Optional[int]:
    """Current `User.sessionVersion`, or None if the user no longer exists."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT "sessionVersion" FROM "User" WHERE id = %s', (str(user_id),))
            row = cur.fetchone()
    if not row:
        return None
    return row["sessionVersion"] if isinstance(row, dict) else row[0]


def _decode_token(authorization: str | None) -> dict:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format.")

    token = authorization.replace("Bearer ", "").strip()

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")

    # Mirrors the frontend's getAuthenticatedUser() sessionVersion check
    # (frontend/src/lib/auth-server.ts) so a password reset invalidates this
    # token for the backend too, not just Next.js page guards -- otherwise a
    # leaked Bearer token would keep working here for up to 7 days after a
    # reset even though the cookie-based frontend session was invalidated.
    # Only enforced when the token actually carries both claims (every
    # current login path does); fails closed -- in doubt, deny.
    user_id = payload.get("userId") or payload.get("id") or payload.get("sub")
    token_session_version = payload.get("sessionVersion")
    if user_id is not None and token_session_version is not None:
        try:
            current_version = _get_session_version(str(user_id))
        except Exception as e:
            print(f"[AUTH] session version check failed for user {user_id}: {e}", flush=True)
            raise HTTPException(status_code=401, detail="Unable to verify session.")
        if current_version is None or current_version != token_session_version:
            raise HTTPException(status_code=401, detail="Session invalidated. Please log in again.")

    return payload


def get_current_user_id(authorization: str = Header(default=None)) -> str:
    payload = _decode_token(authorization)
    user_id = payload.get("userId") or payload.get("id") or payload.get("sub")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload.")

    return str(user_id)


def get_current_user_role(authorization: str = Header(default=None)) -> str:
    """Role claim from the JWT (Iteration 8's `User.role`), defaulting to
    'owner' for tokens that omit it (issued before RBAC roles existed, or any
    other client that doesn't send the claim)."""
    payload = _decode_token(authorization)
    return str(payload.get("role") or "owner")


# Iteration 11 (FR-32): write access is everyone except 'auditor' (read-only).
_WRITE_ROLES = {"owner", "accountant", "admin"}


def require_write_role(authorization: str = Header(default=None)) -> str:
    """Raise 403 unless the token's role can perform write operations.
    Audit-logs the denial (RBAC_WRITE_DENIED) before raising. Returns the
    role string so callers that already need it don't have to decode twice."""
    payload = _decode_token(authorization)
    role = str(payload.get("role") or "owner")

    if role not in _WRITE_ROLES:
        user_id = payload.get("userId") or payload.get("id") or payload.get("sub") or "unknown"
        _log_audit_event(str(user_id), "RBAC_WRITE_DENIED", f"role='{role}' attempted a write operation")
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted to perform this action.")

    return role


def require_admin_role(authorization: str = Header(default=None)) -> str:
    """Raise 403 unless the token's role is 'admin'."""
    payload = _decode_token(authorization)
    role = str(payload.get("role") or "owner")

    if role != "admin":
        user_id = payload.get("userId") or payload.get("id") or payload.get("sub") or "unknown"
        _log_audit_event(str(user_id), "RBAC_WRITE_DENIED", f"role='{role}' attempted an admin-only action")
        raise HTTPException(status_code=403, detail="Admin role required.")

    return role


def _log_audit_event(user_id: str, event_type: str, content: str) -> None:
    """Best-effort write to ActivityLog (FR-33, NFR-15). Never raises -- a
    logging failure must not block the actual operation it's logging."""
    if not DATABASE_URL:
        return
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'INSERT INTO "ActivityLog" (id, "userId", type, content, "createdAt") '
                    "VALUES (%s, %s, %s, %s, NOW())",
                    (str(uuid.uuid4()), str(user_id), event_type, content),
                )
            conn.commit()
    except Exception as e:
        print(f"[AUDIT] failed to log {event_type} for user {user_id}: {e}", flush=True)


def _send_po_approval_email(
    document_id: str,
    po_status: str,
    approved_by: str,
    supplier_name: str,
    total_amount: str,
    currency: str,
) -> None:
    """IT-27b — Send an email notification when a PO is approved or rejected.
    Uses SMTP settings from .env. Silent on any failure."""
    import smtplib
    from email.mime.text import MIMEText

    smtp_host  = os.getenv("SMTP_HOST", "")
    smtp_port  = int(os.getenv("SMTP_PORT", "587"))
    smtp_user  = os.getenv("SMTP_USER", "")
    smtp_pass  = os.getenv("SMTP_PASS", "")
    mail_from  = os.getenv("MAIL_FROM", smtp_user)
    notify_to  = os.getenv("PO_NOTIFY_EMAIL", smtp_user)

    if not smtp_host or not smtp_user or not notify_to:
        print("[EMAIL] SMTP not configured — skipping PO notification.", flush=True)
        return

    status_word = "APPROVED" if po_status == "approved" else "REJECTED"
    emoji = "✅" if po_status == "approved" else "❌"
    subject = f"{emoji} PO {document_id} {status_word} — SME-GPT"
    body = (
        f"Purchase Order {document_id} has been {status_word}.\n\n"
        f"Approved/Rejected by: {approved_by}\n"
        f"Supplier: {supplier_name or 'N/A'}\n"
        f"Total: {currency} {total_amount or 'N/A'}\n\n"
        f"Log in to SME-GPT to view the document."
    )

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"]    = mail_from
    msg["To"]      = notify_to

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(mail_from, [notify_to], msg.as_string())
    print(f"[EMAIL] PO notification sent to {notify_to} for {document_id} ({po_status})", flush=True)


# =========================
# NORMALIZATION HELPERS
# =========================
def safe_text(value: Any, default: str = "NULL") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def safe_number(value: Any):
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return "NULL"

    cleaned = (
        text.replace(",", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .replace("LKR", "")
        .replace("$", "")
        .strip()
    )

    if not cleaned:
        return "NULL"

    try:
        return float(cleaned)
    except Exception:
        return "NULL"


def normalize_items(items: Any):
    if not isinstance(items, list):
        return []

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue

        normalized.append({
            "description": safe_text(item.get("description", ""), default=""),
            "quantity": safe_number(item.get("quantity")),
            "unit_price": safe_number(item.get("unit_price")),
            "line_total": safe_number(item.get("line_total")),
        })
    return normalized


def to_preview_data(fields: dict) -> dict:
    arithmetic = fields.get("arithmetic_validation", {}) or {}

    return {
        "document_type": safe_text(fields.get("document_type")),
        "order_id": safe_text(fields.get("order_id")),
        "flow_type": safe_text(fields.get("flow_type")),
        "company_name": safe_text(fields.get("company_name")),
        "supplier_name": safe_text(fields.get("supplier_name")),
        "date": safe_text(fields.get("date")),
        "currency": safe_text(fields.get("currency")),
        "raw_total_amount": safe_number(fields.get("raw_total_amount")),
        "final_total_amount": safe_number(fields.get("final_total_amount")),
        "payable_amount": safe_number(fields.get("payable_amount")),
        "cash_return": safe_number(fields.get("cash_return")),
        "tax_amount": safe_number(fields.get("tax_amount")),
        "tax_rate": safe_number(fields.get("tax_rate")),
        "cash_inflowed": safe_number(fields.get("cash_inflowed")),
        "cash_outflowed": safe_number(fields.get("cash_outflowed")),
        "category": safe_text(fields.get("category", "Unknown")),
        "received_status": safe_text(fields.get("received_status")),
        "paid_status": safe_text(fields.get("paid_status")),
        "language": safe_text(fields.get("language", "unknown")),
        "items": normalize_items(fields.get("items", [])),
        "arithmetic_status": safe_text(fields.get("arithmetic_status", "not_checked")),
        "arithmetic_validation": arithmetic,
        "raw_text": safe_text(fields.get("raw_text"), default=""),
        "corrected_text": safe_text(fields.get("corrected_text"), default=""),
        "recommended_total_from_items": safe_number(fields.get("recommended_total_from_items")),
    }


def merge_edited_preview_into_fields(original_fields: dict, edited_preview: dict) -> dict:
    merged = deepcopy(original_fields)

    editable_keys = [
        "document_type",
        "order_id",
        "flow_type",
        "company_name",
        "supplier_name",
        "date",
        "currency",
        "raw_total_amount",
        "final_total_amount",
        "payable_amount",
        "cash_return",
        "tax_amount",
        "tax_rate",
        "cash_inflowed",
        "cash_outflowed",
        "received_status",
        "paid_status",
        "language",
        "items",
        "raw_text",
        "corrected_text",
    ]

    for key in editable_keys:
        if key not in edited_preview:
            continue

        if key == "items":
            merged[key] = normalize_items(edited_preview.get("items", []))
        elif key in {"raw_total_amount", "final_total_amount", "payable_amount", "cash_return", "tax_amount", "tax_rate", "cash_inflowed", "cash_outflowed"}:
            merged[key] = safe_number(edited_preview.get(key))
        else:
            merged[key] = safe_text(edited_preview.get(key))

    merged["status"] = "confirmed"

    # Auto-derive effective_flow_type and category after merge
    eft = derive_effective_flow_type(
        merged.get("flow_type"),
        merged.get("received_status"),
        merged.get("paid_status"),
        cash_inflowed=merged.get("cash_inflowed"),
        cash_outflowed=merged.get("cash_outflowed"),
        final_total=merged.get("final_total_amount"),
    )
    merged["effective_flow_type"] = eft
    merged["category"] = derive_category(eft)

    # Auto-set status fields when fully paid/received
    if eft == "cash_inflow" and str(merged.get("received_status", "")).lower() != "received":
        merged["received_status"] = "received"
    if eft == "cash_outflow" and str(merged.get("paid_status", "")).lower() != "paid":
        merged["paid_status"] = "paid"

    return merged


# =========================
# DOCUMENT FILE HELPERS
# =========================
def get_saved_image_url(document_id: str):
    # Fast path: locally cached file (same machine that uploaded it).
    for ext in [".png", ".jpg", ".jpeg", ".webp"]:
        path = SAVED_DOCS_DIR / f"{document_id}{ext}"
        if path.exists():
            return f"/saved-documents/{path.name}"
    # Cross-machine: fall back to a signed Supabase Storage URL (absolute).
    import storage_client
    if storage_client.is_enabled():
        return storage_client.get_signed_url(document_id)
    return None


def save_document_image_from_session(session_meta: dict, document_id: str):
    src = session_meta.get("standard_image")
    if not src:
        return None

    src_path = Path(src)
    if not src_path.exists():
        return None

    ext = src_path.suffix.lower() if src_path.suffix else ".png"
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        ext = ".png"

    dst = SAVED_DOCS_DIR / f"{document_id}{ext}"
    shutil.copy(src_path, dst)

    # Persist to Supabase Storage so the image is viewable from any machine that
    # shares this database (best-effort; the local copy remains as a fast cache).
    import storage_client
    if storage_client.is_enabled():
        storage_client.upload_image(dst, document_id)

    return f"/saved-documents/{dst.name}"


def build_document_detail(user_id: str, document_id: str):
    record = get_record_by_id_for_user(user_id=user_id, document_id=document_id)
    if not record:
        return None

    effective_flow = record.get("effective_flow_type") or record.get("flow_type")

    return {
        **record,
        "original_flow_type": record.get("flow_type"),
        "flow_type": effective_flow,
        "image_url": get_saved_image_url(document_id),
    }


def _build_field_chunk_map(extracted: dict, spatial_chunks: dict) -> dict:
    """GAP-18C (FR-24/26): fuzzy-match extracted field values → chunk_id for click-to-source."""
    from difflib import SequenceMatcher
    all_chunks = [c for p in spatial_chunks.get("pages", []) for c in p.get("chunks", [])]
    field_chunk_map: dict[str, str] = {}
    for field in ("company_name", "supplier_name", "order_id", "date",
                  "raw_total_amount", "final_total_amount", "currency"):
        value = str(extracted.get(field, "") or "").strip()
        if not value or value.upper() == "NULL":
            continue
        best_id, best_ratio = None, 0.0
        for chunk in all_chunks:
            ratio = SequenceMatcher(None, value.lower(), (chunk.get("text") or "").lower()).ratio()
            if ratio > best_ratio:
                best_ratio, best_id = ratio, chunk.get("chunk_id")
        if best_ratio >= 0.55 and best_id:
            field_chunk_map[field] = best_id
    return field_chunk_map


def build_dashboard_summary(records: List[dict]):
    total = len(records)
    invoice = sum(1 for r in records if str(r.get("document_type", "")).strip().lower() == "invoice")
    receipt = sum(1 for r in records if str(r.get("document_type", "")).strip().lower() == "receipt")
    po = sum(1 for r in records if str(r.get("document_type", "")).strip().lower() == "po")
    dn = sum(1 for r in records if str(r.get("document_type", "")).strip().lower() == "dn")

    total_payable = 0.0
    total_receivable = 0.0

    for r in records:
        flow = str(r.get("effective_flow_type") or r.get("flow_type", "")).strip().lower()
        # Use final_total_amount as primary — payable_amount is semantically wrong for receivables.
        amount = safe_number(r.get("final_total_amount"))
        if amount == "NULL":
            amount = safe_number(r.get("payable_amount"))
        if amount == "NULL":
            amount = safe_number(r.get("raw_total_amount"))
        amount = 0.0 if amount == "NULL" else float(amount)

        if flow in ("payable", "cash_outflow"):
            total_payable += amount
        elif flow in ("receivable", "cash_inflow"):
            total_receivable += amount

    # GAP-18D: PENDING / READY counts for UI-D2 dashboard mockup
    pending_count = sum(
        1 for r in records if str(r.get("status", "ready")).strip().lower() == "processing"
    )
    ready_count = total - pending_count

    return {
        "total_documents": total,
        "invoice_count": invoice,
        "receipt_count": receipt,
        "po_count": po,
        "dn_count": dn,
        "total_payable_amount": round(total_payable, 2),
        "total_receivable_amount": round(total_receivable, 2),
        "pending_processing_count": pending_count,
        "ready_for_query_count": ready_count,
    }

def derive_effective_flow_type(
    flow_type: str,
    received_status: str,
    paid_status: str,
    cash_inflowed: Any = None,
    cash_outflowed: Any = None,
    final_total: Any = None,
) -> str:
    flow     = str(flow_type or "").strip().lower().replace(" ", "_")
    received = str(received_status or "").strip().lower()
    paid     = str(paid_status or "").strip().lower()
    inflowed  = safe_number(cash_inflowed)
    outflowed = safe_number(cash_outflowed)
    total     = safe_number(final_total)
    inflowed  = float(inflowed)  if isinstance(inflowed,  (int, float)) else 0.0
    outflowed = float(outflowed) if isinstance(outflowed, (int, float)) else 0.0
    total     = float(total)     if isinstance(total,     (int, float)) else 0.0

    if flow == "receivable":
        if received == "received" or (total > 0 and inflowed >= total):
            return "cash_inflow"
        return "receivable"

    if flow == "payable":
        if paid == "paid" or (total > 0 and outflowed >= total):
            return "cash_outflow"
        return "payable"

    # Already a terminal state
    if flow in ("cash_inflow", "cash_outflow"):
        return flow

    return flow


def derive_category(effective_flow_type: str) -> str:
    eft = str(effective_flow_type or "").strip().lower().replace(" ", "_")
    if eft in ("receivable", "cash_inflow"):
        return "Revenue"
    if eft in ("payable", "cash_outflow"):
        return "Expenses"
    return "Unknown"
# =========================
# REQUEST MODELS
# =========================
class ConfirmSaveRequest(BaseModel):
    session_id: str
    edited_preview: dict
    force_save: bool = False


class ManualDocumentRequest(BaseModel):
    """IT: Manual document entry — no OCR, data entered by the user."""
    document_type: str = "receipt"       # receipt | invoice | po | dn
    order_id: Optional[str] = None       # auto-generated if blank
    date: Optional[str] = None           # defaults to today
    supplier_name: str = ""              # the other party
    currency: str = "LKR"
    items: List[dict] = []               # [{description, quantity, unit_price, line_total}]
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    notes: Optional[str] = None
    due_date: Optional[str] = None       # for PO/Invoice
    force_save: bool = False
    flow_type_override: Optional[str] = None  # from company-role dropdown


class QueryRequest(BaseModel):
    company_name: str
    question: str
    conversation_id: Optional[str] = None


class ChatRequest(BaseModel):
    company_name: str
    question: str
    thread_id: Optional[str] = None   # omit to start a new conversation
    document_id: Optional[str] = None  # Stage C: scope the conversation to one document


class UpdateDocumentRequest(BaseModel):
    company_name: Optional[str] = None
    supplier_name: Optional[str] = None
    date: Optional[str] = None
    document_type: Optional[str] = None
    order_id: Optional[str] = None
    flow_type: Optional[str] = None
    currency: Optional[str] = None
    raw_total_amount: Optional[Any] = None
    final_total_amount: Optional[Any] = None
    payable_amount: Optional[Any] = None
    cash_return: Optional[Any] = None
    cash_inflowed: Optional[Any] = None
    cash_outflowed: Optional[Any] = None
    received_status: Optional[str] = None
    paid_status: Optional[str] = None
    status: Optional[str] = None
    language: Optional[str] = None
    items: Optional[List[dict]] = None
    tax_amount: Optional[Any] = None
    tax_rate: Optional[Any] = None
    # IT-27 — PO approval workflow
    po_status: Optional[str] = None
    approved_by: Optional[str] = None


# =========================
# ROUTES
# =========================
@app.get("/health")
def health():
    from llm_client import (
        check_deepseek_health, check_gemini_health,
        GEMINI_API_KEY, DEEPSEEK_API_KEY, QUERY_PROVIDER, PIPELINE_PROVIDER,
    )
    deepseek = check_deepseek_health()
    gemini = check_gemini_health() if GEMINI_API_KEY else {"ok": False, "error": "GEMINI_API_KEY not set"}
    return {
        "success": True,
        "message": "Backend is running.",
        "llm_provider": QUERY_PROVIDER,
        "pipeline_llm_provider": PIPELINE_PROVIDER,
        "gemini": gemini,
        "deepseek": deepseek,
    }


@app.post("/process-document")
async def process_document(
    file: UploadFile = File(...),
    authorization: str = Header(default=None),
):
    user_id = get_current_user_id(authorization)
    require_write_role(authorization)
    temp_dir = tempfile.mkdtemp(prefix="smegpt_")

    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file uploaded.")

        ext = Path(file.filename).suffix.lower()
        allowed_exts = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

        if ext not in allowed_exts:
            raise HTTPException(
                status_code=400,
                detail="Unsupported file type. Use PDF, PNG, JPG, JPEG, or WEBP.",
            )

        temp_file_path = os.path.join(temp_dir, file.filename)
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        try:
            result = process_uploaded_document(temp_file_path)
        except OCREngineUnavailableError as e:
            raise HTTPException(status_code=503, detail=str(e))
        fields = result["extracted_fields"]
        preview = to_preview_data(fields)

        session_id = str(uuid.uuid4())
        save_processing_session(session_id, user_id, {
            "_created_at": time.time(),
            "user_id": user_id,
            "fields": fields,
            "preview": preview,
            "meta": {
                "uploaded_file": result.get("uploaded_file"),
                "standard_image": result.get("standard_image"),
                "selected_ocr_version": result.get("selected_ocr_version"),
                "ocr_scores": result.get("ocr_scores", {}),
                "ocr_failures": result.get("ocr_failures", {}),
                # Iteration 9 — spatial data for confirm-save embedding
                "safe_boxes": result.get("safe_boxes", []),
                "rich_spatial_chunks_template": result.get("rich_spatial_chunks", {}),
            }
        })

        return {
            "success": True,
            "message": "Document processed successfully.",
            "session_id": session_id,
            "preview": preview,
            "meta": {
                "uploaded_file": result.get("uploaded_file"),
                "standard_image": result.get("standard_image"),
                "selected_ocr_version": result.get("selected_ocr_version"),
                "ocr_scores": result.get("ocr_scores", {}),
                "ocr_failures": result.get("ocr_failures", {}),
                "arithmetic_status": fields.get("arithmetic_status", "not_checked"),
                "arithmetic_validation": fields.get("arithmetic_validation", {}),
            }
        }

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/process-document-stream")
async def process_document_stream(
    file: UploadFile = File(...),
    authorization: str = Header(default=None),
):
    user_id = get_current_user_id(authorization)
    require_write_role(authorization)
    content = await file.read()
    file_size_kb = round(len(content) / 1024, 1)
    filename = file.filename or "document"

    ext = Path(filename).suffix.lower()
    if ext not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
        async def _bad_type():
            yield f"data: {json.dumps({'stage': 'error', 'step': 0, 'message': 'Unsupported file type. Use PDF, PNG, JPG, JPEG or WEBP.'})}\n\n"
        return StreamingResponse(_bad_type(), media_type="text/event-stream")

    progress_q: Queue = Queue()
    done_event = threading.Event()

    def emit(stage: str, step: int, message: str, **extra):
        progress_q.put({"stage": stage, "step": step, "message": message, **extra})

    def run():
        temp_dir = tempfile.mkdtemp(prefix="smegpt_")
        session_id = str(uuid.uuid4())
        try:
            temp_path = os.path.join(temp_dir, filename)
            with open(temp_path, "wb") as fh:
                fh.write(content)

            # Stage 1 — PDF / image conversion
            emit("pdf_conversion", 1, "Converting document to images…")

            from document_pipeline import (
                save_uploaded_file,
                standardize_to_images,
                preprocess_images,
                _normalize_multi_page_ocr_result,
                OCREngineUnavailableError,
                COLAB_OCR_URL as _COLAB_URL,
            )
            from colab_ocr_client import send_images_to_colab_ocr
            from ocr_selector import select_best_ocr_version
            from llm_correction import llm_refine_text, clean_ocr_text
            from ocr_to_json_extractor import extract_structured_json_from_text
            from arithmetic_validator import validate_arithmetic
            from correction_engine import correct_extracted_fields

            raw_file = save_uploaded_file(temp_path)
            _t = time.time()
            orig_images = standardize_to_images(raw_file)
            print(f"[PROCESS-STREAM] PDF->images: {len(orig_images)} page(s) in {time.time()-_t:.1f}s", flush=True)
            _t = time.time()
            processed_pages = preprocess_images(orig_images)
            print(f"[PROCESS-STREAM] preprocess: {len(processed_pages)} page(s) in {time.time()-_t:.1f}s", flush=True)

            if not processed_pages:
                emit("error", 1, "No pages found in the uploaded document.")
                return

            # Stage 2 — OCR
            colab_url = _COLAB_URL or os.getenv("COLAB_OCR_URL", "").strip()
            page_count = len(processed_pages)

            if not colab_url:
                emit("error", 2, str(OCREngineUnavailableError()))
                return

            emit("ocr", 2, f"Running Colab OCR on {page_count} page(s)…")
            print(f"[PROCESS-STREAM] POSTing {page_count} page(s) to Colab OCR…", flush=True)
            _t = time.time()
            try:
                ocr_result = send_images_to_colab_ocr(processed_pages, colab_url)
                print(f"[PROCESS-STREAM] Colab OCR returned in {time.time()-_t:.1f}s", flush=True)
            except Exception as ocr_err:
                print(f"[PROCESS-STREAM] Colab OCR failed: {ocr_err}", flush=True)
                emit("error", 2, str(OCREngineUnavailableError()))
                return

            normalized = _normalize_multi_page_ocr_result(ocr_result)
            versions = normalized.get("versions", {})
            failures = normalized.get("failures", {})

            if not versions:
                emit("error", 2, f"OCR returned no usable text. Failures: {failures}")
                return

            selection = select_best_ocr_version(versions)
            selected_version = selection["selected_version"]
            selected_text = selection["selected_text"]

            if not selected_text.strip():
                failure_detail = ""
                if failures:
                    sample = list(failures.items())[:3]
                    failure_detail = " | ".join(f"{k}: {str(v)[:120]}" for k, v in sample)
                emit("error", 2,
                     f"OCR returned empty text. "
                     f"{'Failure details: ' + failure_detail if failure_detail else 'No text was extracted — check that COLAB_OCR_URL is set to your live ngrok URL and the Colab notebook is running.'}"
                )
                return

            selected_text = clean_ocr_text(selected_text)

            # Iteration 9 — C1 per-box safe correction + C2 rich spatial chunks
            from llm_correction import correct_boxes_for_page as _correct_boxes
            from spatial_serialization import build_spatial_chunks as _build_rich
            _selected_pages = selection.get("selected_pages") or []
            _safe_boxes: list = []
            for _pe in _selected_pages:
                _tl = _pe.get("text_lines", []) if isinstance(_pe, dict) else []
                _safe_boxes.append(_correct_boxes(_tl))
            _c1_pages = [{"page": i + 1, "boxes": pb} for i, pb in enumerate(_safe_boxes)]
            _rich_template: dict = {}
            if _c1_pages:
                try:
                    _rich_template = _build_rich(
                        _c1_pages, tenant_id="__pending__", document_id="__pending__"
                    )
                except Exception as _pre_rich_err:
                    print(f"[PROCESS-STREAM] pre-rich template failed (non-fatal): {_pre_rich_err}", flush=True)

            # Stage 3 — LLM correction
            emit("llm_correction", 3, "Correcting OCR text with LLM…")
            corrected_text = llm_refine_text(selected_text)

            # Stage 4 — Structured extraction
            emit("extraction", 4, "Extracting structured fields…")
            extracted_json = extract_structured_json_from_text(selected_text)
            extracted_json["document_type"] = (
                str(extracted_json.get("document_type", "unknown")).strip().lower() or "unknown"
            )
            extracted_json["corrected_text"] = corrected_text
            extracted_json["raw_text"] = selected_text
            extracted_json["ocr_selected_version"] = selected_version
            extracted_json["ocr_scores"] = selection.get("scores", {})
            extracted_json["ocr_failures"] = failures

            extracted_json = correct_extracted_fields(extracted_json)
            arithmetic_validation = validate_arithmetic(extracted_json)
            extracted_json["arithmetic_validation"] = arithmetic_validation
            extracted_json["arithmetic_status"] = arithmetic_validation.get("status", "not_checked")
            extracted_json["file_size_kb"] = file_size_kb

            recommended = arithmetic_validation.get("recommended", {}) or {}
            if extracted_json["arithmetic_status"] == "mismatch":
                if recommended.get("final_total_amount") is not None:
                    extracted_json["final_total_amount"] = recommended["final_total_amount"]
                if recommended.get("payable_amount") is not None:
                    extracted_json["payable_amount"] = recommended["payable_amount"]

            preview = to_preview_data(extracted_json)

            save_processing_session(session_id, user_id, {
                "_created_at": time.time(),
                "user_id": user_id,
                "fields": extracted_json,
                "preview": preview,
                "meta": {
                    "uploaded_file": str(raw_file),
                    "standard_image": processed_pages[0]["orig"],
                    "selected_ocr_version": selected_version,
                    "ocr_scores": selection.get("scores", {}),
                    "ocr_failures": failures,
                    # Iteration 9 — spatial data for confirm-save embedding
                    "safe_boxes": _safe_boxes,
                    "rich_spatial_chunks_template": _rich_template,
                },
            })

            # FR-33: audit log for streaming upload
            _log_audit_event(user_id, "DOCUMENT_UPLOAD",
                             f"Streamed upload processed: {str(raw_file)}")

            emit(
                "done", 4, "Processing complete.",
                session_id=session_id,
                preview=preview,
                meta={
                    "uploaded_file": str(raw_file),
                    "standard_image": processed_pages[0]["orig"],
                    "selected_ocr_version": selected_version,
                    "ocr_scores": selection.get("scores", {}),
                    "ocr_failures": failures,
                    "arithmetic_status": extracted_json.get("arithmetic_status"),
                    "arithmetic_validation": extracted_json.get("arithmetic_validation", {}),
                },
            )

        except Exception as exc:
            import traceback
            emit("error", 0, str(exc), trace=traceback.format_exc())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            done_event.set()

    threading.Thread(target=run, daemon=True).start()

    async def generate():
        while True:
            try:
                event = progress_q.get_nowait()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("stage") in ("done", "error"):
                    break
            except Empty:
                if done_event.is_set() and progress_q.empty():
                    break
                await asyncio.sleep(0.05)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/sessions/{session_id}/image")
async def get_session_image(session_id: str, authorization: str = Header(default=None)):
    """Return the temp preview image for a not-yet-saved bulk session."""
    from fastapi.responses import FileResponse
    user_id = get_current_user_id(authorization)
    session = load_processing_session(session_id)
    if not session or session.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    src = session.get("meta", {}).get("standard_image")
    if not src or not Path(str(src)).exists():
        raise HTTPException(status_code=404, detail="Preview image not available")
    media = "image/png"
    ext = Path(str(src)).suffix.lower()
    if ext in (".jpg", ".jpeg"):
        media = "image/jpeg"
    elif ext == ".webp":
        media = "image/webp"
    return FileResponse(str(src), media_type=media)


@app.post("/confirm-save")
def confirm_save(payload: ConfirmSaveRequest, authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    require_write_role(authorization)
    session = load_processing_session(payload.session_id)

    if not session:
        raise HTTPException(
            status_code=404,
            detail="Session expired or not found. Please process the document again.",
        )

    if str(session.get("user_id")) != str(user_id):
        raise HTTPException(
            status_code=403,
            detail="This processing session does not belong to the current user.",
        )

    # All keys produced by to_preview_data() — editable + read-only display fields
    _ALLOWED_PREVIEW_KEYS = {
        "document_type", "order_id", "flow_type", "company_name", "supplier_name",
        "date", "currency", "raw_total_amount", "final_total_amount", "payable_amount",
        "cash_return", "tax_amount", "tax_rate",
        "cash_inflowed", "cash_outflowed", "category",
        "received_status", "paid_status", "language", "items",
        "raw_text", "corrected_text",
        # read-only display fields sent back by the frontend unchanged
        "arithmetic_status", "arithmetic_validation", "recommended_total_from_items",
    }
    unknown_keys = set(payload.edited_preview.keys()) - _ALLOWED_PREVIEW_KEYS
    if unknown_keys:
        raise HTTPException(status_code=400, detail=f"Unknown preview fields: {sorted(unknown_keys)}")

    original_fields = session["fields"]
    final_data = merge_edited_preview_into_fields(original_fields, payload.edited_preview)

    # ── Override company_name with the user's registered company ─────────────
    # The OCR may have extracted the ISSUING party's name (e.g. the shop's name
    # on a receipt). We always replace it with the user's own company so that
    # company_name = "AIESEC" (or whatever the user registered) on every document.
    # supplier_name remains as the OTHER party extracted by OCR.
    try:
        with get_db_connection() as _uc:
            with _uc.cursor() as _cur:
                _cur.execute(
                    'SELECT "companyName" FROM "User" WHERE id = %s',
                    (str(user_id),)
                )
                _urow = _cur.fetchone()
        if _urow:
            _user_company = (_urow.get("companyName") if isinstance(_urow, dict) else _urow[0]) or ""
            if _user_company.strip():
                # If supplier_name is still empty, move the OCR-extracted company name there
                if not final_data.get("supplier_name") or \
                   str(final_data.get("supplier_name", "")).strip().upper() in ("", "NULL"):
                    _ocr_company = str(final_data.get("company_name", "")).strip()
                    if _ocr_company and _ocr_company.lower() != _user_company.lower():
                        final_data["supplier_name"] = _ocr_company
                final_data["company_name"] = _user_company.strip()
    except Exception as _uc_err:
        print(f"[CONFIRM-SAVE] company override failed (non-fatal): {_uc_err}", flush=True)

    duplicate = find_duplicate_record(final_data, user_id=user_id)
    if duplicate and not payload.force_save:
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "duplicate_found": True,
                "message": "This document already exists in the repository.",
                "existing_document_id": duplicate.get("document_id", "NULL")
            }
        )

    save_input_json(final_data, "last_confirmed.json")
    save_result = upsert_confirmed_record(final_data, user_id=user_id)

    _record = (save_result.get("record") or {})
    document_id = _record.get("document_id")
    if not document_id:
        raise HTTPException(status_code=500, detail="Failed to retrieve saved document ID.")
    image_url = save_document_image_from_session(session["meta"], document_id)

    # ── Post-save enrichment — runs in a background thread ───────────────
    # Spatial embedding + C4 entity indexing are best-effort and DB/CPU heavy
    # (embedding model + several extra round-trips). Running them inline made
    # the save response wait — and risked the frontend's 60s timeout when a
    # Supabase connection dropped mid-work. They now run after the response is
    # sent, so the save returns instantly and can never time out on this work.
    _safe_boxes = session.get("meta", {}).get("safe_boxes", [])
    _saved_action = save_result["action"]

    def _post_save_enrich():
        # 1) Iteration 9: embed spatial chunks → pgvector + persist JSON blobs
        if _safe_boxes and _saved_action == "inserted":
            try:
                from spatial_serialization import build_spatial_chunks as _bsc
                from vector_index import flatten_chunks_for_embedding, embed_rows, upsert_chunk_embeddings
                from embedding_service import get_embedding_service

                _c1_pages = [
                    {"page": i + 1, "boxes": pb}
                    for i, pb in enumerate(_safe_boxes)
                ]
                _rich = _bsc(_c1_pages, tenant_id=user_id, document_id=document_id)
                _rows = flatten_chunks_for_embedding(_rich)
                if _rows:
                    _embedded = embed_rows(_rows, service=get_embedding_service())
                    upsert_chunk_embeddings(_embedded)
                    print(f"[CONFIRM-SAVE] {len(_embedded)} chunks embedded for {document_id}", flush=True)

                # Persist JSON blobs directly via psycopg (no upsert_confirmed_record rewrite)
                _safe_json = json.dumps(_safe_boxes, ensure_ascii=False)
                _chunks_json = json.dumps(_rich, ensure_ascii=False)
                with get_db_connection() as _conn:
                    with _conn.cursor() as _cur:
                        _cur.execute(
                            'UPDATE "FinancialDocument" '
                            'SET "safeboxJson"=%s, "spatialChunksJson"=%s, "updatedAt"=NOW() '
                            'WHERE "tenantId"=%s AND "documentId"=%s AND "deletedAt" IS NULL',
                            (_safe_json, _chunks_json, str(user_id), str(document_id)),
                        )
                    _conn.commit()
                print(f"[CONFIRM-SAVE] spatial blobs persisted for {document_id}", flush=True)

                # GAP-18C: build field→chunk map for click-to-source in ProvenancePanel (FR-24/26)
                try:
                    _field_chunk_map = _build_field_chunk_map(final_data, _rich)
                    _fcm_json = json.dumps(_field_chunk_map, ensure_ascii=False)
                    with get_db_connection() as _conn2:
                        with _conn2.cursor() as _cur2:
                            _cur2.execute(
                                'UPDATE "FinancialDocument" '
                                'SET "fieldChunkMapJson"=%s, "updatedAt"=NOW() '
                                'WHERE "tenantId"=%s AND "documentId"=%s AND "deletedAt" IS NULL',
                                (_fcm_json, str(user_id), str(document_id)),
                            )
                        _conn2.commit()
                except Exception as _fcm_err:
                    print(f"[CONFIRM-SAVE] field-chunk-map failed (non-fatal): {_fcm_err}", flush=True)
            except Exception as _emb_err:
                print(f"[CONFIRM-SAVE] embedding/persist failed (non-fatal): {_emb_err}", flush=True)

        # 2) GAP-18F: wire C4 entity index (DocLinks)
        try:
            from entity_index import index_document as _index_doc
            _index_doc(final_data, user_id)
            print(f"[CONFIRM-SAVE] C4 entity index updated for {document_id}", flush=True)
        except Exception as _c4_err:
            print(f"[CONFIRM-SAVE] C4 entity index failed (non-fatal): {_c4_err}", flush=True)

        # 3) Order reconciliation — cascade PO status from the order's Delivery
        #    Notes (PO → DN → Invoice state machine). Saving a DN can flip its
        #    linked PO to partially_delivered / fulfilled, and vice-versa.
        _order_id = str(final_data.get("order_id", "") or "").strip()
        if _order_id and _order_id.upper() not in ("NULL", "NONE"):
            try:
                from order_reconciliation import reconcile_order as _reconcile
                _summary = _reconcile(_order_id, user_id)
                if _summary.get("changes"):
                    print(f"[CONFIRM-SAVE] order {_order_id} reconciled: {_summary['changes']}", flush=True)
            except Exception as _rec_err:
                print(f"[CONFIRM-SAVE] order reconciliation failed (non-fatal): {_rec_err}", flush=True)

    threading.Thread(target=_post_save_enrich, daemon=True).start()
    # ─────────────────────────────────────────────────────────────────────

    delete_processing_session(payload.session_id)

    _log_audit_event(user_id, "DOCUMENT_SAVED", f"document_id={document_id} action={save_result['action']}")

    return {
        "success": True,
        "duplicate_found": bool(duplicate),
        "message": "Document saved successfully.",
        "document_id": document_id,
        "image_url": image_url,
        "action": save_result["action"],
        "record": save_result["record"]
    }


@app.put("/documents/{document_id}")
def update_document(document_id: str, payload: UpdateDocumentRequest, authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    require_write_role(authorization)
    update_data = payload.dict(exclude_unset=True)

    if "items" in update_data:
        update_data["items"] = normalize_items(update_data.get("items", []))

    for numeric_key in ["raw_total_amount", "final_total_amount", "payable_amount", "cash_return", "cash_inflowed", "cash_outflowed", "tax_amount", "tax_rate"]:
        if numeric_key in update_data:
            update_data[numeric_key] = safe_number(update_data[numeric_key])

    existing = get_record_by_id_for_user(user_id=user_id, document_id=document_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found.")

    next_flow_type      = update_data.get("flow_type",        existing.get("flow_type", ""))
    next_received       = update_data.get("received_status",  existing.get("received_status", ""))
    next_paid           = update_data.get("paid_status",      existing.get("paid_status", ""))
    next_cash_inflowed  = update_data.get("cash_inflowed",    existing.get("cash_inflowed"))
    next_cash_outflowed = update_data.get("cash_outflowed",   existing.get("cash_outflowed"))
    next_final_total    = update_data.get("final_total_amount", existing.get("final_total_amount"))

    effective_flow_type = derive_effective_flow_type(
        next_flow_type, next_received, next_paid,
        cash_inflowed=next_cash_inflowed,
        cash_outflowed=next_cash_outflowed,
        final_total=next_final_total,
    )
    update_data["effective_flow_type"] = effective_flow_type
    update_data["category"] = derive_category(effective_flow_type)

    # Auto-set status fields on full payment/receipt
    if effective_flow_type == "cash_inflow":
        update_data["received_status"] = "received"
    if effective_flow_type == "cash_outflow":
        update_data["paid_status"] = "paid"

    flow_changed_message = ""
    prev_flow = str(existing.get("effective_flow_type") or existing.get("flow_type") or "").strip().lower()
    if effective_flow_type == "cash_inflow" and prev_flow == "receivable":
        flow_changed_message = "Document fully received — flow changed from Receivable to Cash Inflow."
    elif effective_flow_type == "cash_outflow" and prev_flow == "payable":
        flow_changed_message = "Document fully paid — flow changed from Payable to Cash Outflow."

    updated = update_record_for_user(
        user_id=user_id,
        document_id=document_id,
        updates=update_data,
    )

    if not updated:
        raise HTTPException(status_code=404, detail="Document not found.")

    _log_audit_event(user_id, "DOCUMENT_UPDATED", f"document_id={document_id}")

    # Order reconciliation: editing a document (e.g. marking a Delivery Note
    # delivered) can change its linked PO's fulfilment status. Run synchronously
    # so the persisted PO status is settled before the UI refetches the order.
    _edit_order_id = str(update_data.get("order_id") or existing.get("order_id") or "").strip()
    if _edit_order_id and _edit_order_id.upper() not in ("NULL", "NONE"):
        try:
            from order_reconciliation import reconcile_order as _reconcile
            _reconcile(_edit_order_id, user_id)
        except Exception as _rec_err:
            print(f"[DOCUMENT_UPDATE] order reconciliation failed (non-fatal): {_rec_err}", flush=True)

    # IT-27b: Send email notification when a PO is approved/rejected
    if update_data.get("po_status") in ("approved", "rejected") and update_data.get("approved_by"):
        try:
            _send_po_approval_email(
                document_id=document_id,
                po_status=update_data["po_status"],
                approved_by=update_data["approved_by"],
                supplier_name=str(existing.get("supplier_name", "") or ""),
                total_amount=str(existing.get("final_total_amount", "") or ""),
                currency=str(existing.get("currency", "LKR") or "LKR"),
            )
        except Exception as _email_err:
            print(f"[EMAIL] PO approval email failed (non-fatal): {_email_err}", flush=True)

    return {
        "success": True,
        "message": "Document updated successfully.",
        "flow_change_message": flow_changed_message,
        "document": updated
    }


@app.get("/documents/{document_id}/order")
def get_document_order(document_id: str, authorization: str = Header(default=None)):
    """Order timeline for the order this document belongs to: the PO → DN →
    Invoice chain (documents sharing its order_id) plus the reconciled PO
    status and delivery/invoice/payment flags. Returns order=null when the
    document has no order_id (nothing to link)."""
    user_id = get_current_user_id(authorization)
    record = get_record_by_id_for_user(user_id, document_id)
    if not record:
        raise HTTPException(status_code=404, detail="Document not found.")

    order_id = str(record.get("order_id") or "").strip()
    if not order_id or order_id.upper() in ("NULL", "NONE"):
        return {"success": True, "order": None}

    from order_reconciliation import build_order_view
    return {"success": True, "order": build_order_view(order_id, user_id)}


@app.delete("/documents/{document_id}")
def delete_document(document_id: str, authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    require_write_role(authorization)

    deleted = delete_record_for_user(
        user_id=user_id,
        document_id=document_id,
    )

    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found.")

    _log_audit_event(user_id, "DOCUMENT_DELETED", f"document_id={document_id}")

    return {
        "success": True,
        "message": "Document deleted successfully.",
        "document_id": document_id,
    }

@app.post("/documents/manual")
def create_manual_document(
    payload: ManualDocumentRequest,
    authorization: str = Header(default=None),
):
    """Create a document from manually entered data (no OCR pipeline).

    Generates a professional PNG image using Pillow templates and stores it
    alongside the structured data — exactly like an OCR-processed document,
    but with source='manual' so it can be filtered/badged in the UI.
    """
    user_id = get_current_user_id(authorization)
    require_write_role(authorization)

    from datetime import date as _date
    from dataset_manager import parse_record_for_output
    from manual_doc_generator import generate_document_image, save_document_image_bytes

    # ── Normalise inputs ──────────────────────────────────────────────────────
    doc_type = str(payload.document_type or "receipt").lower().strip()
    doc_date = str(payload.date or "").strip() or _date.today().strftime("%d/%m/%Y")

    # Auto-generate reference number if not provided
    prefix_map = {"receipt": "R", "invoice": "IN", "po": "PO", "dn": "DN"}
    prefix = prefix_map.get(doc_type, "DOC")
    from dataset_manager import generate_document_id
    order_id = str(payload.order_id or "").strip() or generate_document_id(doc_type)

    # ── Look up user's company name ───────────────────────────────────────────
    user_company = ""
    try:
        with get_db_connection() as _uc:
            with _uc.cursor() as _cur:
                _cur.execute('SELECT "companyName" FROM "User" WHERE id = %s', (str(user_id),))
                _row = _cur.fetchone()
        if _row:
            user_company = (_row.get("companyName") if isinstance(_row, dict) else _row[0]) or ""
    except Exception as _e:
        print(f"[MANUAL] could not fetch company name: {_e}", flush=True)

    # ── Calculate totals from items ───────────────────────────────────────────
    items = []
    subtotal = 0.0
    for item in (payload.items or []):
        desc  = str(item.get("description", "") or "").strip()
        if not desc:
            continue
        try:
            qty   = float(str(item.get("quantity",   1)   or 1))
            price = float(str(item.get("unit_price", 0)   or 0).replace(",", ""))
            total = round(qty * price, 2) if price else float(
                str(item.get("line_total", 0) or 0).replace(",", "")
            )
        except (ValueError, TypeError):
            qty, price, total = 1, 0.0, 0.0
        subtotal += total
        items.append({"description": desc, "quantity": qty, "unit_price": price, "line_total": total})

    tax_rate = payload.tax_rate or 0.0
    tax_amount = round(subtotal * tax_rate / 100, 2) if tax_rate else (payload.tax_amount or 0.0)
    grand_total = round(subtotal + tax_amount, 2)

    # Auto-set flow_type — use the role-aware override from the frontend dropdown
    # if provided, otherwise fall back to the default for the document type.
    _VALID_FLOW_TYPES = {
        "payable", "receivable", "cash_inflow", "cash_outflow",
        "expense", "income", "unknown",
    }
    flow_map = {
        "receipt": "cash_outflow",
        "invoice": "receivable",
        "po":      "payable",
        "dn":      "expense",
    }
    _override = str(payload.flow_type_override or "").strip().lower()
    if _override and _override in _VALID_FLOW_TYPES:
        flow_type = _override
    else:
        flow_type = flow_map.get(doc_type, "cash_outflow")

    # ── Build data dict (same shape as OCR extraction output) ─────────────────
    doc_data: dict = {
        "document_type":      doc_type,
        "order_id":           order_id,
        "flow_type":          flow_type,
        "effective_flow_type": flow_type,
        "company_name":       user_company,
        "supplier_name":      str(payload.supplier_name or "").strip() or (
                                  "Customer" if flow_type in ("receivable","cash_inflow")
                                  else "Supplier"),
        "date":               doc_date,
        "currency":           str(payload.currency or "LKR"),
        "raw_total_amount":   grand_total,
        "final_total_amount": grand_total,
        "payable_amount":     grand_total,
        "cash_return":        "",
        "tax_amount":         tax_amount or "",
        "tax_rate":           tax_rate or "",
        "received_status":    "NULL",
        "paid_status":        "paid" if doc_type == "receipt" else "not_paid",
        "status":             "ready",
        "language":           "en",
        "items_json":         __import__("json").dumps(items, ensure_ascii=False),
        "corrected_text":     f"Manual entry: {doc_type} {order_id}",
        "raw_text":           "",
        "ocr_selected_version": "manual",
        "structured_json":    "",
        "correction_json":    "",
        "arithmetic_status":  "matched",
        "arithmetic_json":    "",
        "source":             "manual",          # marks this as manual entry
        "notes":              str(payload.notes or ""),
        "due_date":           str(payload.due_date or ""),
    }

    # ── Duplicate check ───────────────────────────────────────────────────────
    duplicate = find_duplicate_record(doc_data, user_id=user_id)
    if duplicate and not payload.force_save:
        return {
            "success": False,
            "duplicate_found": True,
            "message": "A similar document already exists.",
            "existing_document_id": duplicate.get("document_id", "NULL"),
        }

    # ── Save to DB ────────────────────────────────────────────────────────────
    save_result = upsert_confirmed_record(doc_data, user_id=user_id)
    document_id = save_result["record"]["document_id"]

    # ── Generate document image ───────────────────────────────────────────────
    image_data = {**doc_data, "order_id": order_id, "items": items}
    image_url = None
    try:
        png_bytes = generate_document_image(image_data)
        image_url = save_document_image_bytes(
            png_bytes, document_id, save_dir=str(SAVED_DOCS_DIR)
        )
    except Exception as _img_err:
        print(f"[MANUAL] image generation failed (non-fatal): {_img_err}", flush=True)

    _log_audit_event(user_id, "DOCUMENT_SAVED",
                     f"document_id={document_id} action=manual_entry")

    # ── Wire C4 entity index ──────────────────────────────────────────────────
    try:
        from entity_index import index_document as _index_doc
        _index_doc(doc_data, user_id)
    except Exception:
        pass

    return {
        "success": True,
        "document_id": document_id,
        "image_url":   image_url,
        "action":      save_result.get("action", "inserted"),
        "message":     f"Document {document_id} created successfully.",
    }


@app.get("/documents")
def get_documents(
    authorization: str = Header(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
):
    """Paginated document list with optional document-date range filter.

    Date filtering happens in Python (not SQL) because OCR dates arrive in many
    formats (DD/MM/YYYY, DD MMM YYYY, MM/DD/YYYY, …) that cannot be compared
    with a SQL text predicate reliably.
    """
    user_id = get_current_user_id(authorization)
    from dataset_manager import parse_record_for_output, filter_records_by_doc_date

    if date_from or date_to:
        # Load all records for tenant, filter by doc date in Python, then paginate
        all_raw = load_records(user_id=user_id)
        all_raw = filter_records_by_doc_date(all_raw, date_from, date_to)
        total   = len(all_raw)
        offset  = (page - 1) * limit
        raw_records = all_raw[offset: offset + limit]
    else:
        total       = count_records(user_id=user_id)
        offset      = (page - 1) * limit
        raw_records = load_records(user_id=user_id, limit=limit, offset=offset)

    page_records = []
    for record in raw_records:
        r = parse_record_for_output(record)
        effective_flow = r.get("effective_flow_type") or r.get("flow_type")
        page_records.append({**r, "original_flow_type": r.get("flow_type"), "flow_type": effective_flow})

    return {
        "success": True,
        "documents": page_records,
        "pagination": {
            "page": page, "limit": limit, "total": total,
            "pages": max(1, (total + limit - 1) // limit),
            "has_next": offset + limit < total,
        },
    }


@app.get("/documents/{document_id}")
def get_document_by_id(document_id: str, authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    document = build_document_detail(user_id=user_id, document_id=document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")

    return {"success": True, "document": document}


@app.get("/dashboard-summary")
def dashboard_summary(authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    from datetime import datetime, timedelta

    # NFR-03: load only the most recent 500 docs for summary
    from dataset_manager import parse_record_for_output
    records = [parse_record_for_output(r) for r in load_records(user_id=user_id, limit=500, offset=0)]
    summary = build_dashboard_summary(records)

    # ── Mismatch alerts ────────────────────────────────────────────────────────
    mismatch_docs = [
        {"document_id": r.get("document_id"), "company_name": r.get("company_name"),
         "date": r.get("date"), "document_type": r.get("document_type")}
        for r in records if str(r.get("arithmetic_status", "")).lower() == "mismatch"
    ][:3]

    # ── Widget 1: Business Health Score ───────────────────────────────────────
    today = datetime.today()
    this_month = today.strftime("%Y-%m")
    last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    def _month_net(month_key: str) -> float:
        total_in = total_out = 0.0
        for r in records:
            ds = str(r.get("date", "") or "").strip()
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
                try:
                    if datetime.strptime(ds[:10], fmt).strftime("%Y-%m") == month_key:
                        amt = 0.0
                        for f in ("payable_amount", "final_total_amount"):
                            v = r.get(f)
                            if v not in (None, "NULL", "", "NONE"):
                                try: amt = float(str(v).replace(",", "")); break
                                except: pass
                        ft = str(r.get("effective_flow_type") or r.get("flow_type") or "").lower()
                        if ft in ("receivable", "cash_inflow", "income"): total_in += amt
                        elif ft in ("payable", "cash_outflow", "expense"): total_out += amt
                    break
                except ValueError:
                    continue
        return round(total_in - total_out, 2)

    net_this = _month_net(this_month)
    net_last = _month_net(last_month)
    has_last_month_data = net_last != 0
    trend_pct = round(((net_this - net_last) / abs(net_last) * 100) if has_last_month_data else 0, 1)
    health_score = {
        "net": net_this,
        "trend_pct": trend_pct,
        "has_last_month_data": has_last_month_data,
        "color": "green" if net_this >= 0 else "red",
        "this_month": this_month,
    }

    # ── Widget 4: Who Owes Me / Who I Owe ─────────────────────────────────────
    top_receivables, top_payables = [], []
    for r in records:
        ft = str(r.get("effective_flow_type") or r.get("flow_type") or "").lower()
        paid = str(r.get("paid_status") or "").lower()
        received = str(r.get("received_status") or "").lower()
        if paid in ("paid", "received") or received == "received": continue
        amt = 0.0
        for f in ("payable_amount", "final_total_amount"):
            v = r.get(f)
            if v not in (None, "NULL", ""):
                try: amt = float(str(v).replace(",", "")); break
                except: pass
        if not amt: continue
        entry = {
            "document_id":   r.get("document_id"),
            "document_type": r.get("document_type"),
            "supplier_name": r.get("supplier_name") or r.get("company_name") or "—",
            "amount":        amt,
            "currency":      r.get("currency") or "LKR",
            "date":          r.get("date") or "",
        }
        if ft in ("receivable", "cash_inflow"): top_receivables.append(entry)
        elif ft in ("payable", "cash_outflow"): top_payables.append(entry)
    top_receivables = sorted(top_receivables, key=lambda x: -x["amount"])[:5]
    top_payables    = sorted(top_payables,    key=lambda x: -x["amount"])[:5]

    return {
        "success": True,
        "total": summary.get("total_documents", 0),
        "invoice": summary.get("invoice_count", 0),
        "receipt": summary.get("receipt_count", 0),
        "po": summary.get("po_count", 0),
        "dn": summary.get("dn_count", 0),
        "recent_documents": records[:5] if records else [],
        "total_payable_amount": summary.get("total_payable_amount", 0.0),
        "total_receivable_amount": summary.get("total_receivable_amount", 0.0),
        "pending_processing_count": summary.get("pending_processing_count", 0),
        "ready_for_query_count": summary.get("ready_for_query_count", 0),
        "mismatch_alerts": mismatch_docs,
        # Feature 2 — Analytics widgets
        "health_score":      health_score,
        "top_receivables":   top_receivables,
        "top_payables":      top_payables,
    }


@app.get("/overdue-alerts")
def overdue_alerts(authorization: str = Header(default=None), days: int = Query(default=30, ge=1, le=365)):
    """IT-23: payables/receivables/invoices that are past due or aging beyond
    `days` days with no settlement. Tenant-scoped; deterministic (no LLM)."""
    user_id = get_current_user_id(authorization)
    from dataset_manager import parse_record_for_output
    from overdue_alerts import compute_overdue_alerts

    records = [parse_record_for_output(r) for r in load_records(user_id=user_id)]
    alerts = compute_overdue_alerts(records, days_threshold=days)
    return {"success": True, "count": len(alerts), "alerts": alerts}


@app.get("/cash-flow")
def cash_flow(
    authorization: str = Header(default=None),
    months: int = Query(default=6, ge=1, le=24),
):
    """IT-20 — Monthly cash inflow / outflow / net for the last N months.
    Groups tenant documents by month and sums amounts per flow category."""
    user_id = get_current_user_id(authorization)
    from dataset_manager import parse_record_for_output
    from datetime import datetime, timedelta
    import calendar

    records = [parse_record_for_output(r) for r in load_records(user_id=user_id, limit=1000, offset=0)]

    # Build month buckets for the last N months
    today = datetime.today()
    buckets: dict[str, dict] = {}
    for m in range(months - 1, -1, -1):
        first = today.replace(day=1) - timedelta(days=m * 28)
        key = first.strftime("%Y-%m")
        buckets[key] = {"month": key, "inflow": 0.0, "outflow": 0.0, "net": 0.0, "count": 0}

    for r in records:
        date_str = str(r.get("date", "") or "").strip()
        if not date_str or date_str.upper() == "NULL":
            continue
        parsed_date = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d/%m/%y"):
            try:
                parsed_date = datetime.strptime(date_str[:10], fmt)
                break
            except ValueError:
                continue
        if not parsed_date:
            continue
        key = parsed_date.strftime("%Y-%m")
        if key not in buckets:
            continue

        amt = 0.0
        for field in ("payable_amount", "final_total_amount", "raw_total_amount"):
            v = r.get(field)
            if v is not None and str(v).upper() not in ("NULL", "", "NONE"):
                try:
                    amt = float(str(v).replace(",", ""))
                    break
                except ValueError:
                    pass

        ft = str(r.get("effective_flow_type") or r.get("flow_type") or "").lower()
        if ft in ("cash_inflow", "receivable", "income"):
            buckets[key]["inflow"] += amt
        elif ft in ("cash_outflow", "payable", "expense"):
            buckets[key]["outflow"] += amt
        buckets[key]["count"] += 1
        buckets[key]["net"] = round(buckets[key]["inflow"] - buckets[key]["outflow"], 2)

    for b in buckets.values():
        b["inflow"] = round(b["inflow"], 2)
        b["outflow"] = round(b["outflow"], 2)

    return {"success": True, "months": months, "data": list(buckets.values())}


@app.get("/documents/{document_id}/related")
def get_related_documents(document_id: str, authorization: str = Header(default=None)):
    """IT-22 — PO→DN→Invoice link view. Returns documents linked to this one
    via shared order_id or entity relationships (DocLink table)."""
    user_id = get_current_user_id(authorization)
    from dataset_manager import parse_record_for_output

    doc = get_record_by_id_for_user(user_id=user_id, document_id=document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    order_id = str(doc.get("order_id") or "").strip()
    supplier = str(doc.get("supplier_name") or "").strip().lower()
    results = []

    # Find documents sharing the same order_id
    if order_id and order_id.upper() not in ("NULL", "", "NONE"):
        all_docs = load_all_records(user_id=user_id)
        for r in all_docs:
            if r.get("document_id") == document_id:
                continue
            if str(r.get("order_id") or "").strip() == order_id:
                results.append({
                    "document_id": r.get("document_id"),
                    "document_type": r.get("document_type"),
                    "date": r.get("date"),
                    "supplier_name": r.get("supplier_name"),
                    "final_total_amount": r.get("final_total_amount"),
                    "currency": r.get("currency", "LKR"),
                    "link_reason": f"Same reference: {order_id}",
                })
    elif supplier and supplier not in ("supplier", "customer"):
        # Fall back: find other docs with same counterparty
        all_docs = load_all_records(user_id=user_id)
        for r in all_docs:
            if r.get("document_id") == document_id:
                continue
            if str(r.get("supplier_name") or "").strip().lower() == supplier:
                results.append({
                    "document_id": r.get("document_id"),
                    "document_type": r.get("document_type"),
                    "date": r.get("date"),
                    "supplier_name": r.get("supplier_name"),
                    "final_total_amount": r.get("final_total_amount"),
                    "currency": r.get("currency", "LKR"),
                    "link_reason": f"Same party: {doc.get('supplier_name')}",
                })

    results = results[:10]
    return {"success": True, "document_id": document_id, "related": results, "count": len(results)}


@app.get("/suppliers")
def get_suppliers(
    authorization: str = Header(default=None),
    q: str = Query(default=""),
):
    """IT-24 — Supplier/Customer directory: aggregates unique counterparties."""
    user_id = get_current_user_id(authorization)
    from dataset_manager import parse_record_for_output

    records = load_all_records(user_id=user_id)
    directory: dict[str, dict] = {}

    for r in records:
        name = str(r.get("supplier_name") or "").strip()
        if not name or name.upper() in ("NULL", "SUPPLIER", "CUSTOMER", ""):
            continue
        if q and q.lower() not in name.lower():
            continue
        key = name.lower()
        if key not in directory:
            directory[key] = {
                "name": name,
                "document_count": 0,
                "total_payable":    0.0,   # all documents flowing out
                "total_receivable": 0.0,   # all documents flowing in
                "total_paid":       0.0,   # outflows actually settled (paid_status=paid)
                "total_received":   0.0,   # inflows actually collected (received_status=received)
                "document_types": set(),
                "last_transaction": "",
            }
        entry = directory[key]
        entry["document_count"] += 1
        ft = str(r.get("effective_flow_type") or r.get("flow_type") or "").lower()
        amt = 0.0
        for field in ("payable_amount", "final_total_amount"):
            v = r.get(field)
            if v is not None and str(v).upper() not in ("NULL", "", "NONE"):
                try:
                    amt = float(str(v).replace(",", ""))
                    break
                except ValueError:
                    pass
        paid_st     = str(r.get("paid_status")     or "").lower()
        received_st = str(r.get("received_status") or "").lower()
        if ft in ("payable", "cash_outflow", "expense"):
            entry["total_payable"] += amt
            if paid_st in ("paid", "received"):
                entry["total_paid"] += amt
        elif ft in ("receivable", "cash_inflow", "income"):
            entry["total_receivable"] += amt
            if received_st in ("received", "paid"):
                entry["total_received"] += amt
        entry["document_types"].add(str(r.get("document_type") or "").lower())
        date_val = str(r.get("date") or "")
        if date_val and date_val > entry["last_transaction"]:
            entry["last_transaction"] = date_val

    result = []
    for entry in sorted(directory.values(), key=lambda x: -x["document_count"]):
        result.append({
            **entry,
            "document_types":   sorted(entry["document_types"]),
            "total_payable":    round(entry["total_payable"],    2),
            "total_receivable": round(entry["total_receivable"], 2),
            "total_paid":       round(entry["total_paid"],       2),
            "total_received":   round(entry["total_received"],   2),
            "net_position":     round(entry["total_receivable"] - entry["total_payable"], 2),
        })

    return {"success": True, "count": len(result), "suppliers": result}


@app.get("/suppliers/{name}/history")
def get_supplier_history(
    name: str,
    authorization: str = Header(default=None),
):
    """IT-40 — Full transaction history for a single counterparty."""
    user_id = get_current_user_id(authorization)
    from dataset_manager import parse_record_for_output
    from urllib.parse import unquote

    clean_name = unquote(name).strip().lower()
    records = [parse_record_for_output(r) for r in load_all_records(user_id=user_id)]

    transactions = []
    for r in records:
        sname = str(r.get("supplier_name") or "").strip().lower()
        if sname != clean_name:
            continue
        amt = 0.0
        for field in ("payable_amount", "final_total_amount"):
            v = r.get(field)
            if v not in (None, "NULL", "", "NONE"):
                try: amt = float(str(v).replace(",", "")); break
                except: pass
        ft = str(r.get("effective_flow_type") or r.get("flow_type") or "").lower()
        transactions.append({
            "document_id":   r.get("document_id"),
            "document_type": r.get("document_type"),
            "date":          r.get("date") or "",
            "amount":        amt,
            "currency":      r.get("currency") or "LKR",
            "flow_type":     ft,
            "paid_status":   r.get("paid_status") or "NULL",
            "received_status": r.get("received_status") or "NULL",
            "invoice_status":  r.get("invoice_status") or "",
            "po_status":       r.get("po_status") or "",
            "notes":           r.get("notes") or "",
        })

    transactions.sort(key=lambda x: x["date"] or "", reverse=True)
    total_paid = sum(t["amount"] for t in transactions if t["flow_type"] in ("payable","cash_outflow","expense"))
    total_recv = sum(t["amount"] for t in transactions if t["flow_type"] in ("receivable","cash_inflow","income"))
    return {
        "success":     True,
        "name":        unquote(name).strip(),
        "transactions": transactions,
        "summary": {
            "count":         len(transactions),
            "total_paid":    round(total_paid, 2),
            "total_received": round(total_recv, 2),
            "net":           round(total_recv - total_paid, 2),
        },
    }


@app.get("/suppliers/duplicates")
def get_supplier_duplicates(authorization: str = Header(default=None)):
    """IT-40 — Find supplier names that look like duplicates (Jaro-Winkler >= 0.88)."""
    user_id = get_current_user_id(authorization)
    from dataset_manager import parse_record_for_output
    import jellyfish  # pip install jellyfish

    records = [parse_record_for_output(r) for r in load_all_records(user_id=user_id)]
    names: list[str] = []
    seen: set[str] = set()
    for r in records:
        n = str(r.get("supplier_name") or "").strip()
        if n and n.upper() not in ("NULL","SUPPLIER","CUSTOMER","") and n.lower() not in seen:
            names.append(n); seen.add(n.lower())

    groups: list[list[str]] = []
    used: set[int] = set()
    for i, a in enumerate(names):
        if i in used: continue
        group = [a]
        for j, b in enumerate(names[i+1:], start=i+1):
            if j in used: continue
            try:
                score = jellyfish.jaro_winkler_similarity(a.lower(), b.lower())
            except Exception:
                score = 0
            if score >= 0.88:
                group.append(b); used.add(j)
        if len(group) >= 2:
            groups.append(group); used.add(i)

    return {"success": True, "groups": groups}


@app.put("/suppliers/merge")
def merge_suppliers(
    payload: dict,
    authorization: str = Header(default=None),
):
    """IT-40 — Rename all documents with names in `aliases` to `canonical_name`."""
    user_id = get_current_user_id(authorization)
    require_write_role(authorization)

    canonical = str(payload.get("canonical_name", "")).strip()
    aliases: list[str] = [str(a).strip().lower() for a in payload.get("aliases", [])]
    if not canonical or not aliases:
        raise HTTPException(status_code=400, detail="canonical_name and aliases required")

    updated = 0
    records = load_all_records(user_id=user_id)
    for r in records:
        sname = str(r.get("supplier_name") or "").strip()
        if sname.lower() in aliases and sname != canonical:
            doc_id = r.get("document_id")
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        'UPDATE "FinancialDocument" SET "supplierName"=%s WHERE "documentId"=%s AND "tenantId"=%s',
                        (canonical, str(doc_id), str(user_id))
                    )
                conn.commit()
            updated += 1

    _log_audit_event(user_id, "SUPPLIERS_MERGED", f"canonical={canonical} updated={updated}")
    return {"success": True, "updated": updated}


# ── IT-41: Payment Reminder ───────────────────────────────────────────────────

@app.post("/documents/{document_id}/send-reminder")
def send_payment_reminder(
    document_id: str,
    payload: dict = Body(default={}),
    authorization: str = Header(default=None),
):
    """IT-41 — Send a payment reminder email for an overdue receivable."""
    user_id = get_current_user_id(authorization)
    from dataset_manager import parse_record_for_output

    records = load_all_records(user_id=user_id)
    doc = next((parse_record_for_output(r) for r in records
                if r.get("document_id") == document_id), None)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    recipient_email = str(payload.get("email", "")).strip()
    if not recipient_email:
        raise HTTPException(status_code=400, detail="email required in request body")

    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")

    if not smtp_host or not smtp_user:
        raise HTTPException(status_code=503, detail="SMTP not configured")

    party   = doc.get("supplier_name") or "Valued Customer"
    amount  = doc.get("final_total_amount") or doc.get("payable_amount") or "0"
    cur     = doc.get("currency") or "LKR"
    due     = doc.get("due_date") or doc.get("date") or ""
    ref     = doc.get("order_id") or document_id

    html_body = f"""
    <div style="font-family:sans-serif;max-width:580px;margin:0 auto">
      <h2 style="color:#2252b5">Payment Reminder</h2>
      <p>Dear {party},</p>
      <p>This is a friendly reminder that the following payment is outstanding:</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <tr><td style="padding:8px;background:#f3f4f6;font-weight:bold">Reference</td><td style="padding:8px">{ref}</td></tr>
        <tr><td style="padding:8px;background:#f3f4f6;font-weight:bold">Amount Due</td><td style="padding:8px;color:#dc2626;font-weight:bold">{cur} {float(str(amount).replace(",","")):.2f}</td></tr>
        <tr><td style="padding:8px;background:#f3f4f6;font-weight:bold">Due Date</td><td style="padding:8px">{due or "As agreed"}</td></tr>
      </table>
      <p>Please arrange payment at your earliest convenience.</p>
      <p style="color:#666;font-size:12px">Generated by SME-GPT · {doc.get("company_name","")}</p>
    </div>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Payment Reminder: {ref} – {cur} {float(str(amount).replace(',','')):.2f}"
        msg["From"]    = smtp_user
        msg["To"]      = recipient_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [recipient_email], msg.as_string())
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Email failed: {e}")

    _log_audit_event(user_id, "REMINDER_SENT", f"document_id={document_id} to={recipient_email}")
    return {"success": True, "message": f"Reminder sent to {recipient_email}"}


# ── IT-44: Exchange Rates ─────────────────────────────────────────────────────

_FX_CACHE: dict = {"rates": {}, "fetched_at": 0.0}

@app.get("/exchange-rates")
def exchange_rates(authorization: str = Header(default=None)):
    """IT-44 — Live LKR exchange rates (cached 6 h)."""
    get_current_user_id(authorization)
    import time, urllib.request, json as _json

    now = time.time()
    if now - _FX_CACHE["fetched_at"] < 21600 and _FX_CACHE["rates"]:
        return {"success": True, "base": "LKR", "rates": _FX_CACHE["rates"]}

    try:
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/LKR", timeout=5) as r:
            data = _json.loads(r.read())
        rates = {k: round(1 / v, 6) for k, v in data.get("rates", {}).items()
                 if k in ("USD","EUR","GBP","SGD","AUD","INR","JPY") and v}
        _FX_CACHE["rates"] = rates
        _FX_CACHE["fetched_at"] = now
        return {"success": True, "base": "LKR", "rates": rates}
    except Exception:
        return {"success": True, "base": "LKR", "rates": _FX_CACHE.get("rates", {})}


# ── IT-45: Document Sharing ───────────────────────────────────────────────────

_SHARE_TOKENS: dict[str, dict] = {}   # token -> {document_id, user_id, expires_at}

@app.post("/documents/{document_id}/share")
def share_document(
    document_id: str,
    authorization: str = Header(default=None),
):
    """IT-45 — Generate a 7-day read-only share link for a document."""
    user_id = get_current_user_id(authorization)
    import secrets, time

    token = secrets.token_urlsafe(24)
    _SHARE_TOKENS[token] = {
        "document_id": document_id,
        "user_id":     str(user_id),
        "expires_at":  time.time() + 7 * 86400,
    }
    _log_audit_event(user_id, "DOCUMENT_SHARED", f"document_id={document_id} token={token[:8]}…")
    return {"success": True, "token": token, "expires_in_days": 7}


@app.get("/shared/{token}")
def view_shared_document(token: str):
    """IT-45 — Public read-only view for a shared document (no auth required)."""
    import time
    from dataset_manager import parse_record_for_output

    entry = _SHARE_TOKENS.get(token)
    if not entry:
        raise HTTPException(status_code=404, detail="Share link not found or expired")
    if time.time() > entry["expires_at"]:
        _SHARE_TOKENS.pop(token, None)
        raise HTTPException(status_code=410, detail="Share link expired")

    records = load_all_records(user_id=entry["user_id"])
    doc = next((parse_record_for_output(r) for r in records
                if r.get("document_id") == entry["document_id"]), None)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "success": True,
        "document": {
            "document_id":        doc.get("document_id"),
            "document_type":      doc.get("document_type"),
            "date":               doc.get("date"),
            "company_name":       doc.get("company_name"),
            "supplier_name":      doc.get("supplier_name"),
            "final_total_amount": doc.get("final_total_amount"),
            "currency":           doc.get("currency"),
            "order_id":           doc.get("order_id"),
            "items_json":         doc.get("items_json"),
        },
        "expires_at": entry["expires_at"],
    }


# ── IT-46: Budget Settings ────────────────────────────────────────────────────

def _load_budget_map(user_id) -> dict:
    """Latest saved monthly budget targets for the tenant, as a plain
    {category: value} dict (values are strings as the frontend stored them).
    Returns {} when none set or on any read error."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT content FROM "ActivityLog" WHERE "userId"=%s AND type=%s '
                    'ORDER BY "createdAt" DESC LIMIT 1',
                    (str(user_id), "BUDGET_SETTINGS")
                )
                row = cur.fetchone()
        if row:
            import json as _json
            content = row.get("content") if isinstance(row, dict) else row[0]
            return _json.loads(content) or {}
    except Exception:
        pass
    return {}


@app.get("/user/budget-settings")
def get_budget_settings(authorization: str = Header(default=None)):
    """IT-46 — Return the user's monthly budget targets per expense category."""
    user_id = get_current_user_id(authorization)
    return {"success": True, "budgets": _load_budget_map(user_id)}


# Each budget category maps to the flow types whose current-month total is its
# "actual" -- the SAME income/expense/cash mapping the audit pack and reports
# use, so the dashboard widget agrees with every other number in the app.
_BUDGET_CATEGORY_FLOWS: dict[str, tuple[str, ...]] = {
    "Revenue":       ("receivable", "cash_inflow", "income"),
    "Budgeted Cost": ("payable", "cash_outflow", "expense"),
    "Cash Inflow":   ("cash_inflow",),
    "Cash Outflow":  ("cash_outflow",),
}

# Categories where spending PAST the target is bad (over budget) vs. income
# targets where exceeding is good. Drives the widget's colour semantics.
_BUDGET_SPEND_CATEGORIES = {"Budgeted Cost", "Cash Outflow"}


@app.get("/user/budget-vs-actual")
def get_budget_vs_actual(authorization: str = Header(default=None)):
    """Pair each saved monthly budget target with the actual current-month
    total for that category, computed deterministically from the tenant's
    records. Only categories the user actually budgeted (target > 0) are
    returned. Powers the dashboard Budget vs Actual widget."""
    user_id = get_current_user_id(authorization)
    from datetime import datetime
    from dataset_manager import parse_record_for_output, filter_records_by_doc_date

    budgets = _load_budget_map(user_id)

    today = datetime.today()
    lo = today.replace(day=1).strftime("%Y-%m-%d")
    hi = today.strftime("%Y-%m-%d")
    all_recs = [parse_record_for_output(r) for r in load_all_records(user_id=user_id)]
    records  = filter_records_by_doc_date(all_recs, lo, hi)

    def _actual(flows: set[str]) -> float:
        return round(sum(
            float(str(r.get("final_total_amount") or 0).replace(",", "") or 0)
            for r in records
            if (r.get("effective_flow_type") or r.get("flow_type", "")).lower() in flows
        ), 2)

    categories = []
    for label, flows in _BUDGET_CATEGORY_FLOWS.items():
        try:
            budget = float(str(budgets.get(label) or 0).replace(",", "") or 0)
        except (TypeError, ValueError):
            budget = 0.0
        if budget <= 0:
            continue  # only surface categories the user actually budgeted
        categories.append({
            "category": label,
            "budget": budget,
            "actual": _actual(set(flows)),
            "is_spend": label in _BUDGET_SPEND_CATEGORIES,
        })

    return {
        "success": True,
        "month": today.strftime("%Y-%m"),
        "currency": "LKR",
        "categories": categories,
    }


@app.put("/user/budget-settings")
def save_budget_settings(
    payload: dict = Body(default={}),
    authorization: str = Header(default=None),
):
    """IT-46 — Save monthly budget targets per expense category."""
    user_id = get_current_user_id(authorization)
    require_write_role(authorization)
    import json as _json

    budgets = payload.get("budgets", {})
    _log_audit_event(user_id, "BUDGET_SETTINGS", _json.dumps(budgets))
    return {"success": True, "message": "Budget settings saved"}


# ── IT-47: Audit Pack Export ─────────────────────────────────────────────────

class AuditPackRequest(BaseModel):
    period: str = "this_month"
    date_from: Optional[str] = None
    date_to: Optional[str] = None


@app.post("/reports/audit-pack")
def export_audit_pack(
    payload: AuditPackRequest,
    authorization: str = Header(default=None),
):
    """Generate a ZIP containing an Excel ledger + JSON summary for the selected period."""
    user_id = get_current_user_id(authorization)
    from datetime import datetime, timedelta
    import io, zipfile, json as _json
    from dataset_manager import parse_record_for_output, filter_records_by_doc_date

    today = datetime.today()
    if payload.period == "this_week":
        lo = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        hi = today.strftime("%Y-%m-%d")
    elif payload.period == "this_month":
        lo = today.replace(day=1).strftime("%Y-%m-%d")
        hi = today.strftime("%Y-%m-%d")
    elif payload.period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        lo  = end.replace(day=1).strftime("%Y-%m-%d")
        hi  = end.strftime("%Y-%m-%d")
    elif payload.period == "this_year":
        lo = today.replace(month=1, day=1).strftime("%Y-%m-%d")
        hi = today.strftime("%Y-%m-%d")
    elif payload.period == "custom":
        lo = str(payload.date_from or "").strip()
        hi = str(payload.date_to   or "").strip()
        if not lo or not hi:
            raise HTTPException(status_code=400, detail="date_from and date_to required for custom period")
    else:
        lo = today.replace(day=1).strftime("%Y-%m-%d")
        hi = today.strftime("%Y-%m-%d")

    # Load all records then filter using the multi-format date parser
    all_recs = [parse_record_for_output(r) for r in load_all_records(user_id=user_id)]
    records  = filter_records_by_doc_date(all_recs, lo, hi)

    # ── Excel ─────────────────────────────────────────────────────────────────
    excel_bytes = b""
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Documents"
        ws.append(["Document ID","Type","Date","Company","Counterparty","Amount","Currency","Flow","Status"])
        for r in records:
            status = r.get("paid_status") or r.get("received_status") or r.get("invoice_status") or ""
            ws.append([
                r.get("document_id",""), r.get("document_type",""), r.get("date",""),
                r.get("company_name",""), r.get("supplier_name",""),
                r.get("final_total_amount",""), r.get("currency","LKR"),
                r.get("effective_flow_type") or r.get("flow_type",""), status,
            ])
        buf = io.BytesIO(); wb.save(buf); excel_bytes = buf.getvalue()
    except Exception as e:
        print(f"[AUDIT-PACK] Excel failed: {e}", flush=True)

    # ── Summary JSON ──────────────────────────────────────────────────────────
    income  = round(sum(float(str(r.get("final_total_amount") or 0).replace(",","") or 0)
                        for r in records
                        if (r.get("effective_flow_type") or r.get("flow_type","")).lower()
                        in ("receivable","cash_inflow","income")), 2)
    expense = round(sum(float(str(r.get("final_total_amount") or 0).replace(",","") or 0)
                        for r in records
                        if (r.get("effective_flow_type") or r.get("flow_type","")).lower()
                        in ("payable","cash_outflow","expense")), 2)
    summary = {
        "period": payload.period, "from": lo, "to": hi,
        "total_documents": len(records),
        "total_income": income, "total_expense": expense,
        "net": round(income - expense, 2),
    }

    # ── ZIP ───────────────────────────────────────────────────────────────────
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if excel_bytes:
            zf.writestr(f"documents_{lo}_to_{hi}.xlsx", excel_bytes)
        zf.writestr("summary.json", _json.dumps(summary, indent=2, ensure_ascii=False))
        for r in records:
            doc_id   = r.get("document_id","")
            img_path = Path(SAVED_DOCS_DIR) / f"{doc_id}.png"
            if img_path.exists():
                zf.write(img_path, f"images/{doc_id}.png")

    zip_buf.seek(0)
    _log_audit_event(user_id, "AUDIT_PACK_EXPORTED", f"period={payload.period} docs={len(records)}")

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        zip_buf, media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=sme_gpt_export_{lo}_{hi}.zip"},
    )


# ── IT-49: WhatsApp Webhook ───────────────────────────────────────────────────

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """IT-49 — Twilio WhatsApp webhook: receives forwarded bill images and queues them."""
    import hmac, hashlib, base64, urllib.parse, urllib.request

    # Validate Twilio signature
    twilio_token   = os.getenv("TWILIO_AUTH_TOKEN", "")
    x_sig          = request.headers.get("X-Twilio-Signature", "")
    body_bytes     = await request.body()
    body_str       = body_bytes.decode("utf-8")
    params         = dict(urllib.parse.parse_qsl(body_str))

    if twilio_token:
        url      = str(request.url)
        sorted_p = "".join(f"{k}{v}" for k, v in sorted(params.items()))
        expected = base64.b64encode(
            hmac.new(twilio_token.encode(), (url + sorted_p).encode(), hashlib.sha1).digest()
        ).decode()
        if not hmac.compare_digest(x_sig, expected):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    from_number = params.get("From", "").replace("whatsapp:", "")
    media_url   = params.get("MediaUrl0", "")
    if not media_url:
        return {"status": "no_media"}

    # Look up user by whatsapp_number stored in User settings
    owner_id = None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT id FROM "User" WHERE "phoneNumber" = %s LIMIT 1',
                    (from_number,)
                )
                row = cur.fetchone()
        if row:
            owner_id = (row.get("id") if isinstance(row, dict) else row[0])
    except Exception:
        pass

    if not owner_id:
        print(f"[WHATSAPP] unknown number {from_number}", flush=True)
        return {"status": "unknown_sender"}

    # Download image from Twilio
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    try:
        req = urllib.request.Request(media_url)
        if twilio_sid and twilio_token:
            creds = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode()).decode()
            req.add_header("Authorization", f"Basic {creds}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            image_bytes = resp.read()
    except Exception as e:
        print(f"[WHATSAPP] image download failed: {e}", flush=True)
        return {"status": "download_failed"}

    # Save image to temp and run through manual doc generator + save
    from manual_doc_generator import save_document_image_bytes
    from dataset_manager import generate_document_id

    doc_id = generate_document_id("receipt")
    save_document_image_bytes(image_bytes, doc_id, save_dir=str(SAVED_DOCS_DIR))

    doc_data = {
        "document_type": "receipt", "order_id": doc_id,
        "flow_type": "cash_outflow", "effective_flow_type": "cash_outflow",
        "supplier_name": f"WhatsApp:{from_number}", "company_name": "",
        "date": "", "currency": "LKR",
        "raw_total_amount": 0, "final_total_amount": 0,
        "payable_amount": 0, "cash_return": "",
        "received_status": "NULL", "paid_status": "not_paid",
        "status": "pending", "language": "en",
        "corrected_text": f"WhatsApp bill from {from_number}",
        "raw_text": "", "items_json": "[]",
        "source": "whatsapp",
    }
    try:
        upsert_confirmed_record(doc_data, user_id=str(owner_id))
        _log_audit_event(str(owner_id), "WHATSAPP_DOCUMENT", f"document_id={doc_id} from={from_number}")
    except Exception as e:
        print(f"[WHATSAPP] save failed: {e}", flush=True)

    return {"status": "queued", "document_id": doc_id}



@app.get("/reports/payables")
def payables_report(authorization: str = Header(default=None)):
    """Payables Analysis — avoids double-counting PO + Invoice for same transaction.

    Logic:
      - Outstanding Invoices  → invoice/receipt docs with paid_status NOT 'paid'
      - Committed POs         → PO docs where po_status is pending/approved (not yet invoiced)
      - Settled               → any payable doc with paid_status = 'paid'

    A PO in 'fulfilled' state is excluded from Committed because a real Invoice should
    already exist for it. A PO in 'pending'/'approved' state is a future obligation only.
    """
    user_id = get_current_user_id(authorization)
    from dataset_manager import parse_record_for_output
    from datetime import datetime

    records = [parse_record_for_output(r) for r in load_all_records(user_id=user_id)]

    outstanding: list[dict] = []   # invoices/receipts not yet paid
    committed:   list[dict] = []   # POs that are pending/approved (promise, no invoice yet)
    settled:     list[dict] = []   # already paid

    def _amt(r: dict) -> float:
        for f in ("payable_amount", "final_total_amount"):
            v = r.get(f)
            if v not in (None, "NULL", "", "NONE"):
                try: return float(str(v).replace(",", ""))
                except: pass
        return 0.0

    def _days_old(date_str: str) -> int:
        if not date_str or date_str == "NULL": return 0
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%B %d, %Y"):
            try:
                d = datetime.strptime(date_str[:10], fmt)
                return (datetime.today() - d).days
            except: pass
        return 0

    # Receivable side — mirrors the payable structure (Outstanding / Settled / Overdue 30+)
    recv_outstanding: list[dict] = []   # unpaid receivables, any age
    recv_settled:      list[dict] = []   # received
    recv_overdue:      list[dict] = []   # unpaid receivables, > 30 days old (subset of outstanding)

    for r in records:
        ft   = str(r.get("effective_flow_type") or r.get("flow_type") or "").lower()
        dt   = str(r.get("document_type") or "").lower()
        paid = str(r.get("paid_status") or "").lower()
        recv = str(r.get("received_status") or "").lower()
        pos  = str(r.get("po_status")   or "").lower()
        amt  = _amt(r)

        # ── Receivable bucket ────────────────────────────────────────────────
        if ft in ("receivable", "cash_inflow") and amt > 0:
            age = _days_old(str(r.get("date") or ""))
            recv_row = {
                "document_id":   r.get("document_id"),
                "document_type": dt,
                "supplier_name": r.get("supplier_name") or r.get("company_name") or "—",
                "amount":        amt,
                "currency":      r.get("currency") or "LKR",
                "date":          r.get("date") or "",
                "days_old":      age,
                "received_status": recv,
            }
            if recv in ("received", "paid"):
                recv_settled.append(recv_row)
            else:
                recv_outstanding.append(recv_row)
                if age > 30:
                    recv_overdue.append(recv_row)
            continue   # a document is either payable or receivable, never both

        # ── Payable bucket ───────────────────────────────────────────────────
        if ft not in ("payable", "cash_outflow", "expense") or amt == 0:
            continue

        row = {
            "document_id":   r.get("document_id"),
            "document_type": dt,
            "supplier_name": r.get("supplier_name") or r.get("company_name") or "—",
            "amount":        amt,
            "currency":      r.get("currency") or "LKR",
            "date":          r.get("date") or "",
            "days_old":      _days_old(str(r.get("date") or "")),
            "paid_status":   paid,
            "po_status":     pos,
            "order_id":      r.get("order_id") or "",
            "notes":         r.get("notes") or "",
        }

        if paid in ("paid", "received"):
            settled.append(row)
        elif dt == "po":
            # PO fulfilled/cancelled → real invoice exists; exclude from committed to avoid double-count
            if pos in ("fulfilled", "cancelled", "rejected"):
                pass   # omit — these should have matching invoices
            else:
                committed.append(row)   # pending/approved/partially_delivered
        else:
            outstanding.append(row)   # invoice/receipt not yet paid

    # Sort by amount descending (overdue by age)
    outstanding.sort(key=lambda x: -x["amount"])
    committed.sort(key=lambda x: -x["amount"])
    settled.sort(key=lambda x: -x["amount"])
    recv_outstanding.sort(key=lambda x: -x["amount"])
    recv_settled.sort(key=lambda x: -x["amount"])
    recv_overdue.sort(key=lambda x: -x["days_old"])

    return {
        "success": True,
        "summary": {
            "outstanding_total": round(sum(x["amount"] for x in outstanding), 2),
            "committed_total":   round(sum(x["amount"] for x in committed),   2),
            "settled_total":     round(sum(x["amount"] for x in settled),     2),
            "outstanding_count": len(outstanding),
            "committed_count":   len(committed),
            "settled_count":     len(settled),
            "recv_outstanding_total": round(sum(x["amount"] for x in recv_outstanding), 2),
            "recv_settled_total":     round(sum(x["amount"] for x in recv_settled),     2),
            "recv_overdue_total":     round(sum(x["amount"] for x in recv_overdue),     2),
            "recv_outstanding_count": len(recv_outstanding),
            "recv_settled_count":     len(recv_settled),
            "recv_overdue_count":     len(recv_overdue),
        },
        "outstanding":      outstanding,
        "committed":        committed,
        "settled":          settled,
        "recv_outstanding": recv_outstanding,
        "recv_settled":     recv_settled,
        "recv_overdue":     recv_overdue,
    }


@app.get("/reports/pnl")
def pnl_report(
    authorization: str = Header(default=None),
    year: int = Query(default=0, ge=0),
    months: int = Query(default=12, ge=1, le=24),
):
    """IT-30 — Monthly Profit & Loss: revenue (receivable+cash_inflow) minus
    expenses (payable+cash_outflow) per month for the last N months."""
    user_id = get_current_user_id(authorization)
    from datetime import datetime, timedelta

    records = load_all_records(user_id=user_id)

    today = datetime.today()
    buckets: dict[str, dict] = {}
    for m in range(months - 1, -1, -1):
        first = today.replace(day=1) - timedelta(days=m * 28)
        key = first.strftime("%Y-%m")
        buckets[key] = {"month": key, "revenue": 0.0, "expenses": 0.0, "net": 0.0, "doc_count": 0}

    for r in records:
        date_str = str(r.get("date", "") or "").strip()
        if not date_str or date_str.upper() == "NULL":
            continue
        parsed_date = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d/%m/%y"):
            try:
                parsed_date = datetime.strptime(date_str[:10], fmt)
                break
            except ValueError:
                continue
        if not parsed_date:
            continue
        if year and parsed_date.year != year:
            continue
        key = parsed_date.strftime("%Y-%m")
        if key not in buckets:
            continue

        amt = 0.0
        for field in ("payable_amount", "final_total_amount", "raw_total_amount"):
            v = r.get(field)
            if v is not None and str(v).upper() not in ("NULL", "", "NONE"):
                try:
                    amt = float(str(v).replace(",", ""))
                    break
                except ValueError:
                    pass

        ft = str(r.get("effective_flow_type") or r.get("flow_type") or "").lower()
        if ft in ("receivable", "cash_inflow", "income"):
            buckets[key]["revenue"] += amt
        elif ft in ("payable", "cash_outflow", "expense"):
            buckets[key]["expenses"] += amt
        buckets[key]["doc_count"] += 1

    total_revenue = 0.0
    total_expenses = 0.0
    result = []
    for b in sorted(buckets.values(), key=lambda x: x["month"]):
        b["revenue"]  = round(b["revenue"], 2)
        b["expenses"] = round(b["expenses"], 2)
        b["net"]      = round(b["revenue"] - b["expenses"], 2)
        total_revenue  += b["revenue"]
        total_expenses += b["expenses"]
        result.append(b)

    return {
        "success": True,
        "months": months,
        "total_revenue": round(total_revenue, 2),
        "total_expenses": round(total_expenses, 2),
        "net_profit": round(total_revenue - total_expenses, 2),
        "data": result,
    }


@app.post("/ask-query")
def ask_query(payload: QueryRequest, authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)

    if not payload.company_name.strip():
        raise HTTPException(status_code=400, detail="Company name is required.")

    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question is required.")

    conversation_id = payload.conversation_id or str(uuid.uuid4())
    conversation_history = []
    if payload.conversation_id:
        try:
            conversation_history = load_conversation_turns(user_id, conversation_id)
        except Exception:
            conversation_history = []

    result = answer_financial_question(
        question=payload.question.strip(),
        company_name=payload.company_name.strip(),
        user_id=user_id,
        conversation_history=conversation_history,
    )

    history_saved = True
    history_error = ""
    history_id = None

    try:
        history_id = save_query_history_to_db(
            user_id=user_id,
            company_name=payload.company_name.strip(),
            question=payload.question.strip(),
            answer=result.get("direct_answer", ""),
            explanation=result.get("explanation", ""),
            metrics=result.get("metrics", {}),
            evidence=result.get("evidence", []),
            source_file=result.get("source_file", ""),
            conversation_id=conversation_id,
        )
    except Exception as save_err:
        history_saved = False
        history_error = str(save_err)

    # GAP-19A: detect cross-document price discrepancies (Iter 19)
    from data_tools import detect_discrepancies_in_evidence
    discrepancies = detect_discrepancies_in_evidence(result.get("evidence", []))

    # GAP-19C: append bilingual discrepancy note to the answer text when applicable
    answer_text = result.get("direct_answer", "")
    if discrepancies and any(d.get("is_discrepancy") for d in discrepancies):
        from pal_answer import build_discrepancy_bilingual_note
        try:
            from llm_correction import count_sinhala_chars
            q = payload.question.strip()
            lang = "si" if count_sinhala_chars(q) >= max(3, len(q) // 4) else "en"
        except Exception:
            lang = "en"
        note = build_discrepancy_bilingual_note(discrepancies, lang=lang)
        if note:
            answer_text = f"{answer_text}\n\n{note}"

    # FR-33: log query event
    _log_audit_event(user_id, "QUERY_EXECUTED",
                     f"Q: {payload.question.strip()[:120]} | co: {payload.company_name.strip()}")

    return {
        "success": result.get("success", True),
        "company_name": payload.company_name.strip(),
        "question": payload.question.strip(),
        "answer": answer_text,
        "explanation": result.get("explanation", ""),
        "evidence": result.get("evidence", []),
        "metrics": result.get("metrics", {}),
        "source_file": result.get("source_file", ""),
        "history_saved": history_saved,
        "history_error": history_error,
        "history_id": history_id,
        "discrepancies": discrepancies,
        "conversation_id": conversation_id,
    }


@app.post("/chat")
def chat(payload: ChatRequest, authorization: str = Header(default=None)):
    """Phase 1 — agentic conversational query engine (backend/agent/), feature-
    flagged alongside /ask-query. The LLM only plans by calling deterministic
    tools; pal_executor/pal_validator do the arithmetic and a grounding guard
    is the last line of defense (see agent/graph.py, agent/guard.py). Runs
    alongside the existing PAL/legacy engine without replacing it."""
    if not AGENT_QUERY_ENGINE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="The conversational agent query engine is not enabled on this deployment.",
        )

    user_id = get_current_user_id(authorization)

    if not payload.company_name.strip():
        raise HTTPException(status_code=400, detail="Company name is required.")
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question is required.")

    from agent.service import chat as agent_chat
    from llm_client import LLMUnavailableError

    try:
        result = agent_chat(
            question=payload.question.strip(),
            user_id=user_id,
            company_name=payload.company_name.strip(),
            thread_id=payload.thread_id,
            document_id=(payload.document_id.strip() or None) if payload.document_id else None,
        )
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    _log_audit_event(user_id, "AGENT_CHAT_EXECUTED",
                     f"Q: {payload.question.strip()[:120]} | co: {payload.company_name.strip()}")

    return {
        "success": result.get("success", True),
        "company_name": payload.company_name.strip(),
        "question": payload.question.strip(),
        "answer": result.get("answer", ""),
        "evidence": result.get("evidence", []),
        "trace": result.get("trace", []),
        "thread_id": result.get("thread_id"),
    }


def _require_agent_engine():
    if not AGENT_QUERY_ENGINE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="The conversational agent query engine is not enabled on this deployment.",
        )


@app.get("/chat/threads")
def list_chat_threads(authorization: str = Header(default=None)):
    """Stage A — ChatGPT-style thread list for the sidebar (newest first)."""
    _require_agent_engine()
    user_id = get_current_user_id(authorization)
    from agent.threads import list_threads
    return {"success": True, "threads": list_threads(user_id)}


@app.get("/chat/threads/{thread_id}")
def get_chat_thread(thread_id: str, authorization: str = Header(default=None)):
    """Stage A — full message replay (content + evidence + trace per turn)."""
    _require_agent_engine()
    user_id = get_current_user_id(authorization)
    from agent.threads import get_thread_messages
    messages = get_thread_messages(thread_id, user_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Thread not found.")
    return {"success": True, "thread_id": thread_id, "messages": messages}


class RenameThreadRequest(BaseModel):
    title: str


@app.patch("/chat/threads/{thread_id}")
def rename_chat_thread(thread_id: str, payload: RenameThreadRequest,
                       authorization: str = Header(default=None)):
    _require_agent_engine()
    user_id = get_current_user_id(authorization)
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Title is required.")
    from agent.threads import rename_thread
    if not rename_thread(thread_id, user_id, payload.title.strip()):
        raise HTTPException(status_code=404, detail="Thread not found.")
    return {"success": True, "thread_id": thread_id, "title": payload.title.strip()}


@app.delete("/chat/threads/{thread_id}")
def delete_chat_thread(thread_id: str, authorization: str = Header(default=None)):
    _require_agent_engine()
    user_id = get_current_user_id(authorization)
    from agent.threads import delete_thread
    if not delete_thread(thread_id, user_id):
        raise HTTPException(status_code=404, detail="Thread not found.")
    _log_audit_event(user_id, "AGENT_CHAT_THREAD_DELETED", f"thread: {thread_id[:80]}")
    return {"success": True, "message": "Thread deleted."}


@app.get("/alerts")
def get_alerts(authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    try:
        from alerts import load_recent_alerts
        return {"success": True, "alerts": load_recent_alerts(user_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load alerts: {e}")


@app.get("/conversations")
def get_conversations(authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    try:
        conversations = load_conversations_for_user(user_id)
        return {"success": True, "conversations": conversations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load conversations: {e}")


@app.get("/conversations/{conversation_id}")
def get_conversation_thread(conversation_id: str, authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    try:
        turns = load_conversation_thread_for_user(user_id, conversation_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load conversation: {e}")
    if not turns:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"success": True, "conversation_id": conversation_id, "turns": turns}


@app.get("/query-history")
def get_query_history(authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    history = load_query_history_for_user(user_id)
    return {"success": True, "history": history}


@app.get("/query-history/{history_id}")
def get_query_history_item(history_id: str, authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    item = get_query_history_item_for_user(user_id, history_id)

    if not item:
        raise HTTPException(status_code=404, detail="Query history item not found.")

    return {"success": True, "item": item}


@app.delete("/query-history/{history_id}")
def delete_query_history_item(history_id: str, authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    require_write_role(authorization)
    deleted = delete_query_history_item_for_user(user_id, history_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Query history item not found.")

    return {"success": True, "message": "Query history deleted successfully."}


@app.delete("/query-history")
def clear_query_history(authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)
    require_write_role(authorization)
    deleted_count = clear_query_history_for_user(user_id)
    return {
        "success": True,
        "message": "Query history cleared successfully.",
        "deleted_count": deleted_count,
    }


# =========================
# Iteration 12 — GDPR data export + account deletion (NFR-14)
# =========================
@app.get("/user/export")
def export_user_data(authorization: str = Header(default=None)):
    user_id = get_current_user_id(authorization)

    # GDPR export intentionally returns ALL records — load_all_records is correct here.
    documents = load_all_records(user_id=user_id)
    query_history = load_query_history_for_user(user_id)

    return {
        "success": True,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "documents": documents,
        "query_history": query_history,
    }


@app.delete("/user/account")
def delete_user_account(authorization: str = Header(default=None)):
    """Hard-deletes every tenant-scoped Postgres row for the caller (FR/NFR-14).
    The frontend separately deletes the `User` row itself (cascades
    TrustedDevice/LoginVerification/ActivityLog/UploadedFile) -- this endpoint
    only owns the tables the backend writes directly via psycopg, which Prisma
    cascades can't reach."""
    user_id = get_current_user_id(authorization)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM "DocLink" WHERE "tenantId" = %s', (user_id,))
            cur.execute('DELETE FROM "ChunkEmbedding" WHERE "tenantId" = %s', (user_id,))
            cur.execute('DELETE FROM "Entity" WHERE "tenantId" = %s', (user_id,))  # cascades EntityAlias
            cur.execute('DELETE FROM "FinancialDocument" WHERE "tenantId" = %s', (user_id,))  # cascades LineItem
            cur.execute('DELETE FROM query_history WHERE user_id = %s', (user_id,))
        conn.commit()

    # FR-33: audit the account deletion (Fix 4 — this event was previously missing)
    _log_audit_event(user_id, "ACCOUNT_DELETED", "All tenant data purged via /user/account")

    return {"success": True, "message": "All document data deleted for this account."}


@app.delete("/admin/audit-logs/prune")
def prune_old_audit_logs(
    authorization: str = Header(default=None),
    days: int = Query(default=365, ge=30),
):
    """NFR-15 — Delete ActivityLog rows older than `days` days (default 365).
    Admin-only via RBAC role check."""
    user_id = get_current_user_id(authorization)
    require_admin_role(authorization)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM "ActivityLog" WHERE "createdAt" < NOW() - INTERVAL \'%s days\'',
                (days,),
            )
            deleted = cur.rowcount
        conn.commit()

    _log_audit_event(user_id, "AUDIT_LOGS_PRUNED",
                     f"Deleted {deleted} rows older than {days} days")
    return {"success": True, "deleted_rows": deleted, "retention_days": days}