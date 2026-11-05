-- Migration 012: document reference numbers + link review status
-- Run with: python backend/scripts/run_migration.py backend/migrations/012_document_references.sql
--
-- Purely additive (ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS), so it
-- is safe to run while older app versions are live: they simply ignore the new
-- columns.
--
-- Why: "orderId" was doing two jobs. On a PO it is the PO number; on invoices,
-- receipts and some delivery notes extraction stored the document's OWN number
-- there. Exact orderId matching therefore missed real PO -> DN -> Invoice ->
-- Receipt links and grouped unrelated documents that merely shared a number.
-- These columns keep "what am I" and "what do I refer to" apart.

ALTER TABLE "FinancialDocument"
  ADD COLUMN IF NOT EXISTS "docNumber"  TEXT,   -- this document's own number (PO no., DN no., invoice no., receipt no.)
  ADD COLUMN IF NOT EXISTS "poRef"      TEXT,   -- the purchase order this document belongs to
  ADD COLUMN IF NOT EXISTS "dnRef"      TEXT,   -- the delivery note an invoice bills for
  ADD COLUMN IF NOT EXISTS "invoiceRef" TEXT;   -- the invoice a receipt / payment settles

-- Link lifecycle for PO / DN / invoice / receipt links:
--   auto       strong match, linked automatically
--   suggested  plausible match, waiting for the user to confirm
--   confirmed  confirmed or created by the user (never overwritten)
--   rejected   dismissed by the user (never suggested again)
-- Existing rows (entity / order_ref links) default to 'auto'.
ALTER TABLE "DocLink"
  ADD COLUMN IF NOT EXISTS "status" TEXT NOT NULL DEFAULT 'auto';

CREATE INDEX IF NOT EXISTS "DocLink_tenant_status_idx"
  ON "DocLink" ("tenantId", "status");
CREATE INDEX IF NOT EXISTS "FinancialDocument_tenant_docNumber_idx"
  ON "FinancialDocument" ("tenantId", "docNumber");
CREATE INDEX IF NOT EXISTS "FinancialDocument_tenant_poRef_idx"
  ON "FinancialDocument" ("tenantId", "poRef");
