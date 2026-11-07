# SME-GPT Backend API Contract

This is the **only** coupling between the frontend and the backend. The frontend
talks to the backend purely over HTTP. Any new backend (any language/framework)
that implements these endpoints with these request/response shapes will work with
the existing frontend **unchanged**.

---

## Ground rules

- **Base URL:** the frontend **hardcodes** `http://127.0.0.1:8000` (see `BACKEND_URL`
  constants in `frontend/src/app/**/page.tsx`). The new backend MUST listen on
  `127.0.0.1:8000`, or you must edit those constants.
- **CORS:** allow origins `http://localhost:3000` and `http://127.0.0.1:3000`,
  all methods, all headers, credentials enabled.
- **Content type:** JSON everywhere except `/process-document-stream` (multipart in,
  SSE out).
- **All success responses include `"success": true`.** Error responses use
  `{ "success": false, "message": "<text>" }` with an appropriate HTTP status.

---

## Auth (important even with a fresh DB)

- Frontend stores a JWT in `localStorage`/`sessionStorage` under the key `token`,
  and sends it on every backend call as:
  `Authorization: Bearer <token>`
- The JWT is **issued by the frontend's own auth** (Next.js API routes + its own
  user DB). It is **HS256**, signed with a secret shared via the `JWT_SECRET` env var.
- The new backend does **not** own users. It must only **verify** the JWT:
  - decode with `JWT_SECRET`, algorithm `HS256`
  - the user id is `payload.userId ?? payload.id ?? payload.sub` (use whichever exists)
  - treat that string as `user_id` and scope all data to it.
- On any auth failure return **HTTP 401** (frontend reacts by clearing `token` and
  redirecting to `/login`). Missing header / non-`Bearer` / expired / invalid → 401.

> Fresh-DB caveat: the frontend keeps issuing tokens against its existing users, so
> as long as the new backend shares `JWT_SECRET` and reads the same id claim,
> auth keeps working without migrating users.

---

## Shared object shapes

### `PreviewData` (extracted document fields, pre-save)
```jsonc
{
  "document_type": "invoice",            // string; "NULL" when absent
  "order_id": "string",
  "flow_type": "payable",                // payable | receivable | income | expense | "NULL"
  "company_name": "string",
  "supplier_name": "string",
  "date": "string",
  "currency": "string",
  "raw_total_amount": 1234.56,           // number, or the string "NULL"
  "final_total_amount": 1234.56,
  "payable_amount": 1234.56,
  "cash_return": 0,
  "received_status": "string",
  "paid_status": "string",
  "language": "english",                 // english | sinhala | unknown
  "items": [
    { "description": "string", "quantity": 1, "unit_price": 10, "line_total": 10 }
  ],
  "arithmetic_status": "ok",             // ok | mismatch | not_checked
  "arithmetic_validation": { },          // object, free-form
  "raw_text": "string",
  "corrected_text": "string",
  "recommended_total_from_items": 1234.56
}
```
> Numeric fields may be the literal string `"NULL"` when missing — the frontend
> tolerates this. Keep that convention or send real `null`/`0` (frontend parses
> with a tolerant `parseAmt`).

### `DocumentRecord` (a saved document)
Same financial fields as `PreviewData`, plus:
```jsonc
{
  "document_id": "string",
  "user_id": "string",
  "original_flow_type": "payable",       // the stored flow_type
  "flow_type": "expense",                // EFFECTIVE flow (see derivation rule)
  "effective_flow_type": "expense",
  "image_url": "/saved-documents/<id>.png"  // null if no image; served by backend
}
```

**Effective flow_type derivation (server-side):**
- `receivable` + `received_status == "received"` → `income`
- `payable` + `paid_status == "paid"` → `expense`
- otherwise unchanged.
`GET /documents` and `GET /documents/{id}` return `flow_type` already set to the
effective value, and keep the raw one in `original_flow_type`.

### `QueryHistoryItem`
```jsonc
{
  "id": "uuid",
  "company_name": "string",
  "question": "string",
  "answer": "string",
  "explanation": "string",
  "metrics": { },        // object
  "evidence": [ ],       // array
  "source_file": "string",
  "created_at": "ISO-8601 string"
}
```

---

## Endpoints

### 1. `POST /process-document-stream`  — upload & process (SSE)
- **Auth:** required.
- **Request:** `multipart/form-data`, field `file` = the document
  (`.pdf .png .jpg .jpeg .webp`).
- **Response:** `text/event-stream`. Each line is `data: <json>\n\n`. Event shape:
  ```jsonc
  { "stage": "ocr", "step": 2, "message": "Running OCR…" }
  ```
  - `step` is `0..4` (frontend uses it to drive a progress stepper).
  - Stages seen by the frontend: `pdf_conversion`(1), `ocr`(2), `llm_correction`(3),
    `extraction`(4), plus terminal `done` and `error`.
  - **Terminal `done` event** must also carry:
    ```jsonc
    {
      "stage": "done", "step": 4, "message": "Processing complete.",
      "session_id": "uuid",
      "preview": { ...PreviewData },
      "meta": { "arithmetic_status": "ok", "arithmetic_validation": {}, ... }
    }
    ```
  - **`error` event:** `{ "stage": "error", "message": "<why>" }` — frontend throws this message.
- Frontend reads `event.preview` and `event.session_id` from the `done` event and
  holds `session_id` for the save step.

### 2. `POST /confirm-save`  — persist the (possibly edited) preview
- **Auth:** required. **Body:**
  ```jsonc
  { "session_id": "uuid", "edited_preview": { ...PreviewData }, "force_save": false }
  ```
- The backend keeps the original extraction in server-side session state keyed by
  `session_id`, merges the editable fields from `edited_preview` over it, and saves.
- **Duplicate found (and `force_save` false):** HTTP 200 with
  ```jsonc
  { "success": false, "duplicate_found": true,
    "message": "Already we have this document.",
    "existing_document_id": "string" }
  ```
- **Saved:**
  ```jsonc
  { "success": true, "duplicate_found": false,
    "message": "Document saved successfully.",
    "document_id": "string", "image_url": "/saved-documents/..|null",
    "action": "inserted|updated", "record": { ...DocumentRecord } }
  ```
- **Session missing/expired:** 404. **Session owned by another user:** 403.

### 3. `GET /documents`  — list current user's documents
- **Auth:** required. **Response:**
  `{ "success": true, "documents": [ ...DocumentRecord ] }`

### 4. `GET /documents/{document_id}`  — one document
- **Auth:** required. **Response:** `{ "success": true, "document": { ...DocumentRecord } }`
  (includes `image_url`). **Not found:** 404.

### 5. `PUT /documents/{document_id}`  — update fields
- **Auth:** required. **Body:** any subset of these editable fields (omit = unchanged):
  ```
  company_name, supplier_name, date, document_type, order_id, flow_type, currency,
  raw_total_amount, final_total_amount, payable_amount, cash_return,
  received_status, paid_status, language, items[]
  ```
- Server recomputes effective flow_type. **Response:**
  ```jsonc
  { "success": true, "message": "Document updated successfully.",
    "flow_change_message": "string (may be empty)",
    "document": { ...DocumentRecord } }
  ```
  **Not found:** 404.

### 5b. `POST /documents/{document_id}/verify-totals`  — clear an arithmetic warning
- **Auth:** required, write role. **Body:** none. A person checked the document and its
  totals are right: `arithmetic_status` becomes `verified` and no amount changes. Changing
  the figures later runs the check again.
- **Response:** `{ "success": true, "document": { ...DocumentRecord } }`.
  **No warning to clear:** 400. **Not found:** 404.

### 6. `DELETE /documents/{document_id}`
- **Auth:** required. **Response:**
  `{ "success": true, "message": "...", "document_id": "string" }`. **Not found:** 404.

### 7. `GET /dashboard-summary`
- **Auth:** required. **Response:**
  ```jsonc
  {
    "success": true,
    "total": 0, "invoice": 0, "receipt": 0, "po": 0, "dn": 0,
    "recent_documents": [ ...DocumentRecord ],   // up to 5 most recent
    "total_payable_amount": 0.0,
    "total_receivable_amount": 0.0
  }
  ```
  - Counts are by `document_type` (`invoice|receipt|po|dn`).
  - `total_payable_amount` / `total_receivable_amount` sum the best available amount
    (`payable_amount` → else `final_total_amount` → else `raw_total_amount`) over
    docs whose flow is `payable` / `receivable`.

### 8. `POST /ask-query`  — natural-language financial question
- **Auth:** required. **Body:** `{ "company_name": "string", "question": "string" }`
  (both required → 400 if blank).
- **Response:**
  ```jsonc
  {
    "success": true,
    "company_name": "string", "question": "string",
    "answer": "string",          // short direct answer
    "explanation": "string",     // full explanation text
    "evidence": [ ],             // array of supporting items
    "metrics": { },              // object of computed figures
    "source_file": "string",
    "history_saved": true, "history_error": "", "history_id": "uuid|null"
  }
  ```
- Side effect: persists a `QueryHistoryItem` for the user (non-fatal if it fails —
  report via `history_saved=false` + `history_error`).

### 9. `GET /query-history`
- **Auth:** required. **Response:** `{ "success": true, "history": [ ...QueryHistoryItem ] }`
  (newest first).

### 10. `GET /query-history/{history_id}`
- **Auth:** required. **Response:** `{ "success": true, "item": { ...QueryHistoryItem } }`.
  **Not found:** 404.

### 11. `DELETE /query-history/{history_id}`
- **Auth:** required. **Response:** `{ "success": true, "message": "..." }`. **Not found:** 404.

### 12. `DELETE /query-history`  — clear all for user
- **Auth:** required. **Response:**
  `{ "success": true, "message": "...", "deleted_count": 0 }`

### 13. Static image serving
- The `image_url` returned by docs (e.g. `/saved-documents/<id>.png`) must be
  fetchable at `http://127.0.0.1:8000<image_url>`. Serve the saved document images
  as static files under that path.

### 14. `GET /health` (optional but handy)
- `{ "success": true, "message": "Backend is running." }`

---

## Behaviors the new backend must reproduce (beyond raw shapes)

1. **Per-user scoping:** every document/query is owned by the JWT's `user_id`; never
   leak another user's data.
2. **Session between process & save:** `/process-document-stream` returns a
   `session_id` referencing server-held extraction; `/confirm-save` consumes it.
   (Original impl used an in-memory dict — fine to replace with anything, but the
   contract is: process issues an id, save redeems it.)
3. **Duplicate detection on save** (returns `duplicate_found`, bypassable with
   `force_save: true`).
4. **Effective flow_type derivation** (see rule above) — the dashboard and totals
   depend on it.
5. **Tolerant numeric handling:** amounts may arrive as `"1,234.56"`, `"Rs. 1234"`,
   `"LKR ..."`, `""`, or `"NULL"`; normalize to a number or null.

---

## What is NOT in this contract (stays in the frontend)

Login, signup, 2FA, password reset, device/session management, profile, and the
activity log are all handled by the **frontend's own Next.js API routes + its own
database (Prisma)**. They never call this backend. Copy the frontend as-is and they
keep working. The new backend only needs to **verify** the JWT those routes issue.
