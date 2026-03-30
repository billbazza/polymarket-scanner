# Project Review

Date: 2026-03-30

## Recommended Improvements

1. Add authentication and route-level authorization before exposing the service anywhere beyond localhost.
   The API currently exposes scan triggers, autonomy triggers, and trade actions as unauthenticated POST routes in `server.py`. With live mode available in `execution.py`, this is the main operational risk.

2. Move long-running scans out of request handlers and stop blocking the FastAPI worker on synchronous work.
   Several `async` endpoints call heavy synchronous scanners directly in `server.py`. A job queue, or at minimum `run_in_threadpool` plus persisted job status, would make the service safer and more responsive.

3. Fix lifecycle management for background resources.
   The app starts the wallet monitor on startup, but there is no matching shutdown path to stop monitor threads or close the shared async HTTP client. That will lead to messy reload behavior, leaked connections, and harder production debugging.

4. Replace import-time schema mutation and ad hoc SQLite migrations with explicit versioned migrations.
   The database initializes and mutates schema on import in `db.py`, and schema evolution depends on repeated `ALTER TABLE` calls that ignore `OperationalError`. That is workable for a personal prototype but fragile across environments and deployments.

5. Eliminate scanner duplication and strengthen the quality gate around shared logic.
   `scanner.py` and `async_scanner.py` duplicate core filtering and pair-testing logic, which invites drift. The test suite is also a single custom script instead of `pytest` plus CI. Extracting shared pure functions and adding standard automated checks would improve maintainability.

## Additional Note

Scan history is currently saved with `pairs_tested=0` and `cointegrated=0` in `server.py` even though both scanners compute those metrics. Fixing that would improve operational telemetry.
