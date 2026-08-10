-- The three platform tables the application writes, and only those.
--
-- 003 gave echomind_app SELECT across infinity, which is right for every read path, and
-- full CRUD on echomind, which is its own state. What it missed is that approving an
-- action writes to the platform: a booking, a service request, or a new user. Nothing
-- caught it, because APP_DATABASE_URL was never set and the API went on connecting as
-- the owner — the least-privilege role was tested for what it could read and never asked
-- to do the job. Pointing the API at it turned every approval into InsufficientPrivilege.
--
-- INSERT alone. The assistant creates records; it never rewrites or removes platform
-- history. A cancellation is a status change made by Infinity X, not a DELETE from here,
-- and the demo's own tear-down runs as the owner because removing seeded rows is
-- scaffolding rather than something the application is ever entitled to do.

GRANT INSERT ON infinity.bookings          TO echomind_app;
GRANT INSERT ON infinity.service_requests  TO echomind_app;
GRANT INSERT ON infinity.users             TO echomind_app;
