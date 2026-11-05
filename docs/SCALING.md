# Scaling & Migration

How SME-GPT grows with its user base **without losing data or breaking things**,
and how to migrate off any single provider. This is the reference behind the
"can it scale?" question.

The design goal is a **stateless, horizontally-scalable backend in front of a
single source of truth (Postgres) and durable object storage**, so capacity is
added by running more identical instances and/or moving the data tier to a
bigger managed plan — never by rewriting the app.

---

## 1. Where state lives (the thing that decides scalability)

An app scales horizontally only if a request can be served by **any** instance.
That requires no per-request state on the instance itself. SME-GPT keeps all
mutable state in shared, external services:

| State | Where it lives | Consequence for scaling |
|---|---|---|
| Financial documents, users, C4 graph, embeddings | **Postgres** (Supabase), tenant-scoped | Shared by all instances; the single source of truth |
| In-progress upload between `/process-document` and `/confirm-save` | **`processing_sessions` table** (migration 010) with a GC sweeper | Was an in-memory dict — now any instance can finish an upload another started; survives restarts |
| Uploaded document images | **Supabase Storage** (object store) via `storage_client.py`, with a local-disk fast cache | Durable and visible cross-instance; a lost/recreated VM loses only the cache, not the image |
| Auth | **Stateless JWT** (+ DB-backed session-version / device-trust) | No server-side session affinity needed |
| Embedding model | Loaded read-only in each process | Not shared state; just per-instance memory |

**Net result:** the backend process holds no authoritative state. You can run N
instances behind a load balancer today. The two classic blockers — the in-memory
session dict and local-only image files — have both been externalised.

> One remaining durability caveat: image save writes the local cache first and
> uploads to Supabase Storage best-effort. If `SUPABASE_URL` /
> `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_STORAGE_BUCKET` are **not** set, images
> are node-local and not durable. Setting them (already supported by
> `storage_client.py`) makes object storage authoritative. Verify with the
> checklist in §6.

---

## 2. Horizontal scaling (add instances)

The backend is a stateless FastAPI/Uvicorn app, so scaling out is "run more of
it behind a load balancer":

- **Liveness** — `GET /healthz`: instant, no I/O, means "process is up".
- **Readiness** — `GET /readyz`: cheap `SELECT 1` through the pool; returns
  **503** when the DB is unreachable so the balancer drains a broken instance
  instead of serving errors. Deliberately does **not** call the LLM — an LLM
  outage degrades gracefully and must not mark an instance unready.
- `GET /health` remains the rich diagnostic (LLM provider reachability) — do not
  point a load balancer at it (it does external calls and reports unhealthy on
  an LLM outage).

Each instance opens its own DB pool. The invariant to respect:

```
(number of backend instances) × DB_POOL_MAX  <  database connection limit
```

Supabase's free pooler allows ~60. With the default `DB_POOL_MAX=8` that is ~7
instances before you must either raise the DB plan or lower the per-instance max.

---

## 3. The data tier scales by plan, not rewrite

Everything durable is standard Postgres, reached two ways that both scale:

- **Backend** → `psycopg` + `psycopg_pool` (`db.py`) through Supabase's
  **transaction pooler (port 6543 / PgBouncer)**. Pool size is env-tunable:
  `DB_POOL_MIN` / `DB_POOL_MAX` (defaults 1 / 8), plus `DB_POOL_MAX_IDLE_SECS`.
- **Frontend** → Prisma, same database.

Query cost stays flat as data grows because reads are indexed on the tenant key:

- `FinancialDocument`: `@@index([tenantId])`, `[tenantId, docDate]`,
  `[tenantId, supplierName]`
- `DocLink` / `Entity` / `EntityAlias`: tenant-scoped composite indexes
- `ChunkEmbedding`: `[tenantId]`, `[tenantId, documentId]`, **HNSW** ANN index
  on the 384-d vector (`vector_cosine_ops`) — RAG stays sub-linear
- `query_history`: `[user_id]`, `[user_id, created_at]` (migration 011 /
  self-healing on startup) — added because history reads previously seq-scanned
- `processing_sessions`: indexed on `user_id` and `created_at`

**Tenant isolation** (`tenantId = user_id` on every read/write) is what makes
this multi-tenant-safe at any size: one shared schema, no per-user tables, no
data bleed. Growth = more rows behind the same indexes.

---

## 4. Migration path (no lock-in, no data loss)

Because the tier is plain Postgres + an S3-compatible object store, moving
providers is a data copy, not a rebuild:

**Database** (Supabase → RDS / Cloud SQL / self-hosted / another Supabase):

```bash
pg_dump "$OLD_DATABASE_URL" --no-owner --no-privileges -Fc -f smegpt.dump
pg_restore --no-owner --no-privileges -d "$NEW_DATABASE_URL" smegpt.dump
# ensure the pgvector extension exists on the target first:
#   CREATE EXTENSION IF NOT EXISTS vector;
```

Then repoint `DATABASE_URL` (backend) and `DATABASE_URL` (frontend) and
redeploy. Schema is reproducible independently of any dump via the numbered
`backend/migrations/*.sql` (run with `scripts/run_migration.py`) and Prisma
migrations.

**Object storage**: copy the bucket (`rclone`/`aws s3 sync` — it is
S3-compatible) and repoint `SUPABASE_URL` / `SUPABASE_STORAGE_BUCKET`. Image URLs
are minted at read time (`get_saved_image_url`), so nothing stores an absolute
host that would break.

**Compute**: the backend is a single Docker image (non-root, `${PORT}`, Poppler
baked in). It runs on any container host — the current Oracle ARM + Coolify, or
Fly/Render/ECS/Cloud Run — unchanged. The frontend is standard Next.js on
Vercel.

No component is pinned to a proprietary API: Postgres is portable, storage is
S3-compatible, the LLM is behind `llm_client.py` (swap DeepSeek ↔ Gemini ↔ any
provider by env), and OCR is behind a URL (`COLAB_OCR_URL`).

---

## 5. Cost-effective growth ladder

Each rung is a config/plan change, not a rewrite:

1. **Now (free tier):** 1 backend instance, Supabase free DB + Storage, Vercel
   hobby. Handles a pilot user base.
2. **More load:** raise `DB_POOL_MAX`, run 2–3 backend instances behind the
   balancer (`/readyz` gates them). Still free DB if under the connection cap.
3. **Data/traffic grows:** upgrade the Supabase (or move to a managed Postgres)
   plan for more connections + storage; bump `DB_POOL_MAX`. HNSW keeps RAG fast.
4. **Heavy embedding/OCR:** move the embedding model and OCR to their own
   workers (both are already off the request path — embedding runs in
   `_post_save_enrich`, OCR is a remote URL) so the API tier stays light.

---

## 6. Pre-scale checklist

- [ ] `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET` set
      (images durable + cross-instance) — confirm with a fresh instance that
      cannot see local files still serving an image.
- [ ] `instances × DB_POOL_MAX < DB connection limit`.
- [ ] Load balancer points liveness at `/healthz`, readiness at `/readyz`
      (never `/health`).
- [ ] `pgvector` extension present on the DB (needed for `ChunkEmbedding`).
- [ ] All `backend/migrations/*.sql` applied on the target DB.
- [ ] A recent `pg_dump` exists before any provider migration.

---

## What is intentionally *not* horizontally scaled

- **Colab OCR** is a demo/dev component (a notebook + ngrok URL) and is out of
  scope for production scaling by design — in production it is replaced by a
  dedicated OCR service behind `COLAB_OCR_URL`, which the pipeline already treats
  as a swappable remote endpoint with a local Surya fallback.
