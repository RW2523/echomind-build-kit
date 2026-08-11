-- What the assistant remembers about you between conversations.
--
-- Strictly preferences, never facts. Golden rule 1 says every number, date and status in
-- an answer comes from a tool result, and memory must not become a second, staler source
-- of those. So nothing here is ever quoted back as an answer: it only pre-fills a
-- proposal, which still goes to you for approval before anything happens.
--
-- Concretely: after you approve a booking on ACC-A1, the next booking you ask for is
-- proposed with ACC-A1 already filled in — and the approval card shows it, so a wrong
-- guess is one click away from being corrected rather than silently acted on.

CREATE TABLE IF NOT EXISTS echomind.user_memory (
    user_id    text        NOT NULL,
    key        text        NOT NULL,
    value      text        NOT NULL,
    -- How many times this was confirmed by an approved action. A preference seen once is
    -- a coincidence; seen four times it is how someone works.
    hits       integer     NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);

CREATE INDEX IF NOT EXISTS user_memory_user_idx ON echomind.user_memory (user_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON echomind.user_memory TO echomind_app;
