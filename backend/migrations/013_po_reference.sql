-- Migration 013: poReference (superseded by poRef from 012_document_references; existing values still link)
-- A DN or invoice links to its purchase order through the PO number printed on
-- it ("PO No" / "Order No" / ඇණවුම් අංකය), stored here. Before this column,
-- `orderId` held each document's OWN number, so most real documents never
-- linked to their PO. See backend/order_reconciliation.py.
-- Run with: python backend/scripts/run_migration.py backend/migrations/013_po_reference.sql

ALTER TABLE "FinancialDocument" ADD COLUMN IF NOT EXISTS "poReference" TEXT;
