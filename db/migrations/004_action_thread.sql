-- 004_action_thread.sql — link an action back to the conversation that proposed it.
--
-- Spec 04 §4: approving an action resumes the graph thread so the confirmation lands in
-- the same chat. The action therefore has to remember which thread it came from.
-- Nullable: actions raised through the API or MCP directly have no conversation.

ALTER TABLE echomind.actions ADD COLUMN IF NOT EXISTS thread_id text;

CREATE INDEX IF NOT EXISTS ix_actions_thread ON echomind.actions (thread_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON echomind.actions TO echomind_app;
