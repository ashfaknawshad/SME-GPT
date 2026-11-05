-- Migration 011: Scaling indexes
-- query_history is read filtered by user_id (usually newest-first) whenever a
-- user opens their history. Without these indexes that read seq-scans the whole
-- table, whose size grows with EVERY user's queries — a linear slowdown as the
-- user base grows. Idempotent; also created on startup by
-- app.ensure_query_history_table() so it self-heals in every environment.
-- Run with: python backend/scripts/run_migration.py backend/migrations/011_scaling_indexes.sql

CREATE INDEX IF NOT EXISTS idx_query_history_user
  ON query_history (user_id);

CREATE INDEX IF NOT EXISTS idx_query_history_user_created
  ON query_history (user_id, created_at DESC);
