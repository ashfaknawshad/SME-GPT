-- Migration 012: Purchase-order reference on Delivery Notes and invoices
-- A DN or invoice links to its purchase order through the PO number printed on
-- it ("PO No" / "Order No" / ඇණවුම් අංකය), stored here. Before this column,
-- `orderId` held each document's OWN number, so most real documents never
-- linked to their PO. See backend/order_reconciliation.py.
-- Numbered 012 (not 011) to avoid colliding with upstream's 011_scaling_indexes.sql.
-- Run with: python backend/scripts/run_migration.py backend/migrations/012_po_reference.sql

ALTER TABLE "FinancialDocument" ADD COLUMN IF NOT EXISTS "poReference" TEXT;
