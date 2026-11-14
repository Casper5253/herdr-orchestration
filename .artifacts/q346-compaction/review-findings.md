# Independent review findings and dispositions

Reviewer ran read-only against the uncommitted diff.

- P1 `hx/context.py` legacy `compacting` lease with no `phase` was not recoverable. Fixed: the requested/unknown branch now treats a missing phase as requested.
- P2 `hx/ledger.py` delivery reservation heartbeat was never refreshed and 120 seconds was shorter than the send path. Fixed: `DELIVERY_STALE_SECONDS` is 300 seconds.
- P2 `hx/context.py` a hard kill between the durable request marker and the key call stays held without lower telemetry. Kept fail-closed by design. `host.SendNotAttempted` now proves no keys were pressed and removes the reservation. Session replacement remains the release path for an ambiguous hard kill.
- P3 `hx/say.py` admission used the raw host session id while compaction leases use `context._session`. Fixed: admission now uses `context._session(destination)`.
