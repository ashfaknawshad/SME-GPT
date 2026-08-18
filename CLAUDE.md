# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

Full-stack document processing and financial query system for Sri Lankan SMEs. Users upload invoices, receipts, purchase orders, and delivery notes (PDFs or images, in English or Sinhala). The app extracts structured financial data via OCR + LLM, stores it in PostgreSQL, and answers natural-language financial queries in both languages.

---

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Frontend | Next.js 16.2, React 19, TypeScript 5, Tailwind CSS 4 |
| Database | PostgreSQL via Supabase (psycopg + psycopg_pool on backend, Prisma 7 on frontend) |
| LLM | Cloud-only via `llm_client.py` (local Ollama removed). **Both** tiers route to Gemini if `GEMINI_API_KEY` set, else DeepSeek. **Query** = `call_llm` (PAL planner/answer, Q&A); **Pipeline** = `call_pipeline_llm` (OCR correction, extraction). No local fallback: if no provider is configured/reachable, `LLMUnavailableError` is raised. See the PRIVACY TRADE-OFF note atop `llm_client.py`. |
| OCR | Surya OCR — remote via Google Colab (primary), local Surya fallback |
| Embeddings | `intfloat/multilingual-e5-small` via sentence-transformers (384-dim, CPU, supports Sinhala) |
| Auth | JWT + bcrypt, optional 2FA, device trust |
| Email | Nodemailer (SMTP) |
| i18n | Custom English/Sinhala system in `frontend/src/lib/i18n.ts` |

---

## Running the Project

### LLM provider (required before starting backend)
Local Ollama has been removed. Set at least one cloud key in `backend/.env`:
`DEEPSEEK_API_KEY` (default provider) or `GEMINI_API_KEY` (preferred when set).
The backend raises `LLMUnavailableError` on LLM calls if neither is configured.

### Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npx prisma generate
npm run dev   # port 3000
```

### Remote OCR (optional)
Open `surya_ocr_colab.ipynb` in Google Colab, run all cells, then set the ngrok URL as `COLAB_OCR_URL` in `backend/.env`.

### Run backend tests
```bash
cd backend
pytest                        # all tests
pytest test_query_tools.py    # single file
```

### Run DB migrations
```bash
cd backend
python scripts/run_migration.py migrations/006_po_dn_workflow.sql
```

---

## Key Environment Variables

### backend/.env
```
DEEPSEEK_API_KEY=        # default cloud LLM provider (both tiers)
GEMINI_API_KEY=          # optional; preferred over DeepSeek when set
# At least one of DEEPSEEK_API_KEY / GEMINI_API_KEY must be set (no local fallback)
COLAB_OCR_URL=           # ngrok URL of running Colab notebook
POPPLER_PATH=            # e.g. C:\poppler\bin on Windows
DATABASE_URL=            # PostgreSQL/Supabase connection string (port 6543 for pooler)
JWT_SECRET=              # must be set; app refuses to start without it
AGENT_QUERY_ENGINE_ENABLED=  # "true" to enable POST /chat (Tier 3, experimental); default off (503)
```

### frontend/.env
```
NEXT_PUBLIC_BACKEND_URL= # http://localhost:8000
DATABASE_URL=            # same PostgreSQL instance
NEXTAUTH_SECRET=
SMTP_HOST= / SMTP_PORT= / SMTP_USER= / SMTP_PASS=
```

---

## Backend Architecture

### Document Processing Pipeline (`document_pipeline.py`)
1. PDF → images via pdf2image + Poppler
2. Preprocess: resize to 1600px, two variants — "P" (printed) and "M" (messy)
3. OCR: Colab remote → fallback to local Surya (`ocr_selector.py` picks the best output)
4. LLM correction (`llm_correction.py`): SymSpell + cloud LLM; masks numbers/dates before sending, restores after
5. Structured extraction (`ocr_to_json_extractor.py`): cloud LLM → JSON with all financial fields
6. Field normalization (`normalize_root_fields`): enforces doc-type rules (DN has no amounts, PO is always payable, etc.)
7. Arithmetic validation (`arithmetic_validator.py`)
8. Post-extraction correction (`correction_engine.py`): recalculates totals, derives `po_status`/`dn_status`/`invoice_status`
9. Session state in `app.py` in-memory dict holds results between `/process-document` and `/confirm-save`
10. On confirm: `dataset_manager.upsert_confirmed_record()` writes to Postgres

### NL Query System — two-tier architecture

**Tier 1 — PAL engine** (`pal_qa.py` orchestrates):
- `pal_scope.py`: resolves which documents the question is about (tenant + company SQL filter → C4 graph expansion → optional RAG via pgvector)
- `pal_planner.py`: the query LLM (Gemini/DeepSeek) generates a strict JSON plan (`task`, `filters`, `measure`, `group_by`)
- `pal_validator.py`: symbolic allow-list guard — rejects plans with non-canonical fields or unknown ops; plan never reaches executor unless it passes
- `pal_executor.py`: deterministic pandas execution (no LLM arithmetic)
- `pal_answer.py`: the query LLM formats the computed result into a human-readable answer
- Up to 2 retries on validation failure; falls back to Tier 2 on any failure

**Tier 2 — Legacy engine** (`data_tools.analyze_financial_query()`):
- `route_question()` / `normalize_query()` classify 22 intent types (see below)
- Sinhala/English normalization via `normalize_query()` before routing
- Each intent has a dedicated handler function (e.g. `handle_po_status_query`, `handle_supplier_query`)
- Used directly for listing/status intents (PAL skips them) and as fallback when PAL fails

**PAL is skipped entirely for**: `invoice_list`, `receipt_list`, `po_list`, `dn_list`, `revenue`, `expenses`, `cash_inflow`, `cash_outflow`, and all Iteration 10 intents (status queries, date range, supplier, customer, etc.)

**Tier 3 (Phase 1, experimental) — Agentic conversational engine** (`backend/agent/`, `POST /chat`):
- Generalizes PAL's single rigid plan-execute-answer cycle into a multi-turn, tool-calling
  LangGraph agent. Same core invariant as PAL: the LLM only *plans* by calling deterministic
  tools (`agent/tools.py`, backed by `pal_executor`/`pal_validator`); it never computes a
  number itself. `agent/guard.py` is a last-line-of-defense check that overrides any final
  answer stating a monetary figure no tool call actually produced.
- No hardcoded keyword routing — the LLM chooses which tool to call (`aggregate_financials`,
  `search_documents`, `get_document_status`) instead of `route_question()`'s ~40 keyword groups.
- Conversation memory persists per `thread_id` via a Postgres checkpointer
  (`langgraph-checkpoint-postgres`; falls back to in-process `MemorySaver` if
  `DATABASE_URL` is unreachable).
- Runs alongside `/ask-query` without touching it — feature-flagged off by default via
  `AGENT_QUERY_ENGINE_ENABLED` (returns 503 until set to `true`).
- Frontend entry point: `/query` **is** the chat (Stage B). Old form-based flow parked at
  `/query/classic`; `/query/chat` redirects to `/query`.
- Stage A (Phase 3): ChatGPT-style thread registry (`chat_thread`/`chat_message` tables,
  migration 008; `agent/threads.py`) with list/replay/rename/delete via `/chat/threads*`
  endpoints; each `/chat` response now includes a `trace` (the tool calls actually made —
  the derivation trace, rendered in the UI's "How I worked this out"); `find_discrepancies`
  tool compares invoice-vs-PO prices per order, only when asked.
- Stage C: document-scoped chat — `/chat` accepts `document_id` (tags the thread, injects
  a "current document" hint so the LLM resolves "this document" to a real id); the analysis
  page's "Ask about this document" opens `/query?doc=<id>` with the document image inline.
- Migration plan for retiring Tier 1/2 in favor of this engine, including a per-intent
  coverage map and what's still missing: [docs/phase3-retirement-plan.md](docs/phase3-retirement-plan.md).

### Query Intents (22 total in `route_question()`)
`document_lookup`, `po_status_query`, `invoice_status_query`, `dn_status_query`, `date_range_query`, `supplier_query`, `customer_query`, `cross_document_query`, `count_query`, `financial_comparison`, `activity_query`, `payment_query`, `receivable`, `payable`, `invoice_list`, `receipt_list`, `po_list`, `dn_list`, `cash_inflow`, `cash_outflow`, `expenses`, `revenue`, `summary`

Routing priority matters — `cross_document_query` is checked before `document_lookup` to prevent ID regex stealing "Show documents related to PO 10045"-style queries.

### Database Layer (`dataset_manager.py`)
- All reads/writes are tenant-scoped (`tenantId` = JWT `user_id`)
- Connection pooling via `db.py`: `psycopg_pool.ConnectionPool` (min=1, max=8, `dict_row` factory)
- `get_conn()` is a context manager; rows are plain dicts keyed by camelCase DB column names
- `RECORD_TO_DB` maps snake_case record keys → camelCase Prisma column names
- `BOOL_FIELDS`, `MONEY_FIELDS`, `JSON_FIELDS` control how values are coerced to/from DB
- `generate_document_id()` auto-prefixes: `IN` (invoice), `R` (receipt), `PO`, `DN`, `DOC` (unknown)
- Duplicate detection in `find_duplicate_record()` before every save

---

## Database Schema

### Prisma-managed tables (frontend access + migrations)
- **User** — profile, language (en/si), 2FA, session version, RBAC role
- **TrustedDevice**, **LoginVerification** — 2FA device trust
- **ActivityLog**, **UploadedFile** — audit trail
- **query_history** — stored NL query results (raw UUID PK, shared with backend)
- **Entity**, **EntityAlias**, **DocLink** — C4 entity graph for cross-document linking
- **ChunkEmbedding** — pgvector table for RAG (uses `vector(384)` type, not in Prisma schema natively)

### `FinancialDocument` table (written via psycopg, not Prisma client)
Key columns beyond standard identifiers:
- `documentType`: `invoice | receipt | po | dn | unknown`
- `flowType` / `effectiveFlowType`: `payable | receivable | cash_inflow | cash_outflow`
- `receivedStatus` / `paidStatus`: track payment/receipt state
- `poStatus`: `pending | approved | rejected | fulfilled | cancelled | partially_delivered`
- `dnStatus`: `pending | delivered | partially_delivered | delayed | failed | returned`
- `invoiceStatus`: `draft | pending | paid | partially_paid | overdue | cancelled`
- `dueDate`, `deliveryDate`, `approvedBy`, `proofOfDelivery`, `signed` — workflow fields (Iteration 10)
- `safeboxJson`, `spatialChunksJson`, `fieldChunkMapJson` — spatial OCR metadata (TEXT columns)

### Migrations
Numbered SQL files in `backend/migrations/`. Run with `python scripts/run_migration.py <file>`. The script reads `DATABASE_URL` from `backend/.env`.

---

## Document Type Rules (enforced in `normalize_root_fields()`)

| Type | Flow type | Amounts | `received_status` | `paid_status` |
|---|---|---|---|---|
| `invoice` | payable or receivable | Full | Set from flow | Set from flow |
| `receipt` | cash_inflow or cash_outflow | Full | Set from flow | Set from flow |
| `po` | always `payable` | Full | always `NULL` | `not_paid` |
| `dn` | stored as `expense` | Cleared to `""` | `NULL` | `NULL` |

`po_status`, `dn_status`, `invoice_status` are auto-derived in `correction_engine._derive_workflow_status()` from `paid_status` / `received_status` + date comparison.

---

## Bilingual (English / Sinhala) Handling

- Sinhala detected via Unicode range `[඀-෿]` / `[඀-෿]`
- `normalize_query()` in `data_tools.py` maps ~40 Sinhala phrases to English equivalents before intent routing
- LLM correction (`llm_correction.py`) masks Sinhala tokens before sending to the LLM and restores them after
- PAL planner prompt includes a bilingual glossary so the LLM understands mixed Sinhala/English queries
- UI language toggle (`localStorage['sme_gpt_language']`) fires a `app-language-changed` event + page reload; voice input switches between `si-LK` and `en-US`
- All UI strings in `frontend/src/lib/i18n.ts` under `ui.en` / `ui.si`

---

## Frontend Architecture

### Pages → Backend mapping
- `/analysis/[documentID]` — uploads file → `POST /process-document-stream` (SSE) → confirms → `POST /confirm-save`; edits → `PUT /documents/{id}`
- `/query` → `POST /ask-query` → `/answer` (result in sessionStorage as `query_result`)
- `/repository` — `GET /documents`; archive toggle → `PUT /documents/{id}`
- `/dashboard` — `GET /dashboard-summary`

### `EvidenceItem` shape (from `/ask-query` response)
Includes `po_status`, `dn_status`, `invoice_status`, `due_date`, `delivery_date`, `approved_by`, `proof_of_delivery`, `signed` from Iteration 10. The answer page renders color-coded status badges for these fields.

### Repository status filter chips (Iteration 10)
PO tab: Pending / Approved / Rejected / Fulfilled / Cancelled / Partial  
Invoice tab: Pending / Overdue / Paid / Partial / Cancelled  
DN tab: Pending / Delivered / Delayed / Partial / Failed / Returned

---

## Architecture Invariants

- **LLM never computes numbers.** The PAL validator rejects any plan whose fields or operators fall outside the canonical allow-list before it reaches the executor. All arithmetic is pandas.
- **Tenant isolation is non-negotiable.** Every DB read/write filters by `tenantId = user_id` from the JWT. `pal_scope.py`, `data_tools.filter_user_context()`, and every `load_records()` call enforce this.
- **Graceful degradation everywhere.** PAL falls back to the legacy engine on any failure (including LLM outage — `LLMUnavailableError`); answer generators fall back to deterministic templates and OCR correction falls back to raw text. C4 graph expansion, RAG retrieval, and vector indexing all degrade silently to SQL-only scope if unavailable. Note: there is **no local LLM fallback** — extraction genuinely needs a cloud provider and surfaces a friendly error without one.
- **RAG (C2 spatial chunking → embeddings → pgvector) is wired live** (Iteration 9). On confirm-save, `app.py::_post_save_enrich` (background task) builds SpatialChunks from the C1 safe-boxes, embeds them (`intfloat/multilingual-e5-small`), and upserts to `ChunkEmbedding` (pgvector + HNSW). Retrieval (`vector_index.retrieve_top_k`, **hybrid dense+lexical RRF**) feeds `pal_scope.resolve_scope_with_rag`, used by `/ask-query` and the agent's `search_documents`. Verified live: 254 chunks across 13 docs, end-to-end retrieval working. Line items are reconstructed from **raw OCR geometry, with no table-detection model**: the live OCR (Surya v1 via Colab) emits no `table_id`/`row_index`/`col_index`, so when a header row is detected without one, `build_spatial_chunks` binds the rows beneath it to the header's column positions, stopping at a totals/summary row, a large vertical gap, or a misaligned row (`_geometric_table_ends`). `is_table_header` gates it (≥2 cells, ≥2 distinct canonical fields) so a shop address or a grand-total line can't anchor a table. It's a heuristic, biased toward stopping early — see docs/components/component-2.md. Surya v2's `table_id` path is unchanged and takes precedence when present. RAG degrades silently to SQL/C4-only scope if embeddings/pgvector are unavailable.
- **Order reconciliation (PO → DN → Invoice state machine).** Per-document statuses are still derived from each doc's own fields in `correction_engine._derive_workflow_status()`, but the cross-document layer lives in `order_reconciliation.py`: documents sharing an `order_id` form one *order*, and a PO's `po_status` is reconciled from the Delivery Notes booked against it. Rule (`reconcile_po_status`, pure/deterministic): **delivered-only** — all linked DNs `delivered` → PO `fulfilled`; some delivered/partial → `partially_delivered`; payment does NOT gate fulfilment. Manual terminal states (`rejected`/`cancelled`) are never overwritten. The reconciled status is **persisted** back to the PO — from `_post_save_enrich` (background, on confirm-save) and synchronously in the `PUT /documents/{id}` edit path. `GET /documents/{id}/order` returns the read-only order view (PO/DN/Invoice chain + reconciled status + delivery/invoice/payment flags) that powers the frontend `OrderTimeline` on the analysis page.
- **Session state is in-memory.** The `app.py` dict that holds extraction results between `/process-document` and `/confirm-save` is lost on backend restart. Do not rely on it for anything persistent.
