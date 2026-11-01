-- Purchase-order reference on Delivery Notes and invoices (links them to their PO).
-- Mirrors backend/migrations/012_po_reference.sql.
ALTER TABLE "FinancialDocument" ADD COLUMN IF NOT EXISTS "poReference" TEXT;
