-- Cancelling and moving a booking, at column granularity.
--
-- 005 granted INSERT and nothing else, on the reasoning that "the assistant creates
-- records; it never rewrites or removes platform history — a cancellation is a status
-- change made by Infinity X, not a DELETE from here". That was right about DELETE and
-- right about history. It was incomplete about cancellation: a user who can book through
-- this assistant and must then telephone the core to cancel has been given half a tool,
-- and the half they are missing is the one with a charge attached.
--
-- So the assistant may now change a booking — but only a booking, and only the three
-- columns a cancellation or a move actually touches. Column-level grants are the whole
-- point: with UPDATE on the table, a bug could rewrite user_id or account_code and move a
-- charge onto someone else's code. It cannot, because the grant does not reach those
-- columns and Postgres refuses rather than trusting the application to be careful.
--
-- Still no DELETE, anywhere. A cancelled booking stays on the record as cancelled; that
-- is what makes it auditable, and removing it would erase the very charge the user may
-- later query.

GRANT UPDATE (status, starts_at, ends_at) ON infinity.bookings TO echomind_app;

COMMENT ON TABLE infinity.bookings IS
  'Bookings. echomind_app may INSERT, and may UPDATE only status, starts_at and ends_at '
  '(cancel and reschedule). It may never UPDATE user_id or account_code, and never '
  'DELETE: a cancelled booking stays on the record as cancelled.';
