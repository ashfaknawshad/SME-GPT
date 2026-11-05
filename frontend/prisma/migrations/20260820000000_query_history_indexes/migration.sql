-- Scaling: index query_history by user_id (reads filter by it, often newest
-- first). IF NOT EXISTS so it is safe alongside the backend's self-healing
-- ensure_query_history_table() and migration 011_scaling_indexes.sql.
CREATE INDEX IF NOT EXISTS "query_history_user_id_idx" ON "query_history"("user_id");
CREATE INDEX IF NOT EXISTS "query_history_user_id_created_at_idx" ON "query_history"("user_id", "created_at");
