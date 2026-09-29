# Remaining shared-feature adoption

These migrations remain in consumer repositories. Preserve each consumer's
policy and verify its tests when making the change.

## Recs: validated configuration edits (F02)

`Cfg.set_attr` in `recs/cfg/cfg.py` still edits a revalidation dump directly.
Replace that edit with `reccy.configuration.update.validated_update`. Keep the
mutable-address allowlist and existing address syntax in recs. Verify nested
edits, cross-field rejection, unchanged originals on failure, and authored
unit values.

## Recs: observable event-client closure (F03)

`watch()` in `recs/daemon/watch.py` still waits on its own completion event,
which a disconnected event stream may never set. Use `EventClient.wait_closed()`
and `terminal_reason` to wake on EOF or failure while preserving the current
snapshot and stop behavior. This does not establish an atomic snapshot/event
handoff or authorize replaying control commands.

## Recs: owned settings claim (F04)

`claim_settings` and `release_settings` in `recs/daemon/instances.py` still
use an exclusive-create PID file and stale-file reclamation. Port the local
ownership guard to `reccy.runtime.claims.ResourceClaim`, retaining recs'
settings-path identity, instance discovery, and user-facing conflict policy.
Use a stable lock path and coordinate the change to avoid two independent
claim schemes during rollout.

## lyte: retry scheduling (F05)

`retry_call` in `lyte/retry.py` still owns its delay and backoff calculations.
Use `reccy.runtime.retry.RetrySchedule` for scheduling, retaining lyte's
cancellable waits, retryable-exception policy, operation timeouts, logging,
and user-visible failure state. Choose the schedule's delay cap explicitly
so it does not silently change lyte's current backoff.

## Delivery rule

Make each migration in its consumer repository with focused tests. Reccy
should not add parallel helper implementations or compatibility paths.

## Additional work beyond the prompt

None.
