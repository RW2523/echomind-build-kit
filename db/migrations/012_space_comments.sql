-- The purpose of the three spaces that never got one.
--
-- 009 gave the five domain spaces a COMMENT ON SCHEMA, because it created them and the
-- reasoning was fresh. `infinity`, `reporting` and `echomind` predate it and carry
-- nothing — which was invisible for as long as nothing read the comments, and became a
-- hole the moment the Data & Tools console started showing each space's purpose.
--
-- The console reads these rather than shipping its own prose, deliberately. A sentence
-- written in a React component describes what someone believed about the schema on the
-- day they wrote it; a sentence attached to the schema travels with the migration that
-- changes it, and `\dn+` shows the same thing the screen does. Three spaces displaying
-- "no purpose recorded" was the honest rendering of that gap, and closing the gap beats
-- hardcoding around it.

COMMENT ON SCHEMA infinity IS
  'The vendor''s system of record, owned by Infinity X. We read it and never reorganise '
  'it; the agent writes no SQL against it directly — the domain spaces are the way in.';

COMMENT ON SCHEMA reporting IS
  'The original four reporting views, and still the SQL allow-list bare names resolve in. '
  'Superseded by the domain spaces for new work, kept because the evals and golden '
  'queries are written against them.';

COMMENT ON SCHEMA echomind IS
  'This application''s own state: knowledge chunks, pending actions, the audit trail, '
  'agent checkpoints and eval runs. Nothing here belongs to Infinity X.';
