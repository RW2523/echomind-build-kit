-- Every "I don't know" is a document someone still has to write.
--
-- The assistant already refuses honestly when the gate or the faithfulness check says
-- it cannot answer, and that refusal is the most useful signal the system produces: it
-- names, in the user's own words, a question the corpus does not cover. Until now it was
-- logged to a trace file and never aggregated, so nobody could act on it.
--
-- One row per refusal. `question_key` is the normalised question, so the same thing asked
-- fifteen different ways collapses into one ranked entry rather than fifteen.

CREATE TABLE IF NOT EXISTS echomind.knowledge_gaps (
    id            bigserial PRIMARY KEY,
    question_key  text        NOT NULL,
    question      text        NOT NULL,
    user_id       text        NOT NULL,
    role          text        NOT NULL,
    -- Which gate stopped it: below_score_floor, no_coverage, sources_disagree,
    -- unfaithful, no_permitted_sources. Tells a curator whether the document is missing
    -- entirely or merely not specific enough.
    reason        text        NOT NULL,
    top_score     double precision,
    -- The nearest thing retrieval could find. Where a curator starts.
    closest_doc   text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS knowledge_gaps_key_idx    ON echomind.knowledge_gaps (question_key);
CREATE INDEX IF NOT EXISTS knowledge_gaps_recent_idx ON echomind.knowledge_gaps (created_at DESC);

GRANT SELECT, INSERT ON echomind.knowledge_gaps TO echomind_app;
GRANT USAGE, SELECT ON SEQUENCE echomind.knowledge_gaps_id_seq TO echomind_app;
