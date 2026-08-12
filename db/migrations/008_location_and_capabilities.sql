-- Facility location and instrument capability — the data "where is the nearest centre
-- that can do cryo-EM?" needs and the platform did not have.
--
-- Two questions users actually ask were unanswerable, not because retrieval was weak but
-- because the facts did not exist: a facility knew its name and code and nothing about
-- where it is, and an instrument knew its hourly rate and nothing about what it does.
-- Everything else in this system refuses when a fact is missing, which is correct and
-- also useless if the fact is one nobody ever recorded.
--
-- Coordinates are stored as plain numerics rather than PostGIS geography: distances here
-- are campus-scale, a handful of sites, and the haversine in server/mcp/tools.py is exact
-- enough at that range. Adding a PostGIS dependency to a demo box to save arithmetic that
-- fits in six lines would be the wrong trade.

ALTER TABLE infinity.facilities
    ADD COLUMN IF NOT EXISTS campus       text,
    ADD COLUMN IF NOT EXISTS building     text,
    ADD COLUMN IF NOT EXISTS room         text,
    ADD COLUMN IF NOT EXISTS address      text,
    ADD COLUMN IF NOT EXISTS latitude     numeric(9,6),
    ADD COLUMN IF NOT EXISTS longitude    numeric(9,6),
    ADD COLUMN IF NOT EXISTS contact_email text,
    ADD COLUMN IF NOT EXISTS opening_hours text;

-- What an instrument is FOR. `techniques` is the searchable surface ("cryo-EM",
-- "single-cell RNA-seq"); `sample_types` and `modality` narrow a recommendation;
-- `specification` is the human-readable detail a scientist actually wants to compare.
ALTER TABLE infinity.instruments
    ADD COLUMN IF NOT EXISTS modality      text,
    ADD COLUMN IF NOT EXISTS techniques    text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS sample_types  text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS specification text,
    ADD COLUMN IF NOT EXISTS room          text;

-- Capability search is a substring/overlap match over a small table, so a GIN index on
-- the arrays is the whole optimisation needed.
CREATE INDEX IF NOT EXISTS idx_instruments_techniques
    ON infinity.instruments USING gin (techniques);
CREATE INDEX IF NOT EXISTS idx_instruments_sample_types
    ON infinity.instruments USING gin (sample_types);
