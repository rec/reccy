# Shared infrastructure features

Consumer migrations are deferred. Implementations and tests here change only
Reccy; no claim is made that sibling projects have adopted these APIs.

## Verified finite asset store

`reccy.runtime.assets.AssetStore` is a private, host-owned filesystem store for
finite opaque bytes. Construct it with a per-user directory, then use
`import_bytes()` to verify SHA-256 and length, publish one immutable object, and
create an immutable acquisition/materialization entry. The entry records a
versioned source key and one of the eight source kinds without treating that key
as proof that two source requests have equal bytes.

```python
store = AssetStore(cache_directory)
entry = store.import_bytes(
    contents,
    source_key='v1:host-request',
    category=AssetCategory.acquired,
    source_kind=SourceKind.local_file,
)
with store.open_entry(entry.id) as file:
    use(file.read())
```

Existing objects are rehashed before reuse. A wrong declared identity raises
`AssetIdentityMismatch`; a missing or changed object raises
`AssetCorruptionError`. `open_entry()` creates a durable lease before returning
a handle and removes it when the context exits. Named references and immutable
pins are explicit retention roots: moving a reference does not retarget an
existing pin.

`RetentionRule` and `RetentionMatch` provide pure, additive rule evaluation for
finite entries. A rule either `protect`s an entry from every collection mode or
`retain`s it until its deadline, which pressure collection may override.
`explain_retention()` reports applicable rules and roots, `plan_collection()` is
read-only, and `collect()` rechecks roots under the store-wide metadata claim
before deleting manifests and then unreachable objects. Rules may select an
asset category, source/media kind, source key, or tags. A missing rule leaves an
unrooted entry eligible. URL/Git acquisition, HTTP freshness, providers and
capture streams remain host-specific later stages of Ufor's asset-cache plan.

## Bounded asset capture

`reccy.runtime.capture.CaptureStore` builds immutable finite capture versions on
an `AssetStore`. Every `CaptureSpec` has a maximum byte budget and exactly one
frame limit, duration limit, or manual-stop mode. It also records resolved media
facts and adapter/encoder versions. Callers select captures by immutable ID or a
named reference; pinning a reference snapshots its current capture ID, so moving
the reference cannot retarget the pin.

For callback and client-buffer sources, `CaptureSession.queue_fragment()` copies
borrowed storage into one preallocated bounded byte queue and does no filesystem
I/O. `drain()` performs object publication outside the producer call. Queue
overflow either raises `CaptureQueueOverflow` or records an explicit native-frame
gap according to `CaptureOverflowPolicy`; it never silently drops data. Stop the
provider using its own protocol, then finalize with EOF, reached-bound, or clean
stop. Finalization closes the queue first, including against an in-flight
producer call.

Abort writes incomplete recovery evidence without publishing a capture.
`salvage()` is deliberately separate: it publishes only verified fragments,
retains the failure reason in recovery evidence, and marks the manifest
`salvaged_failure` rather than claiming the requested extent completed. Fragment
entries are pinned as they are drained so a writer interruption cannot leave a
published manifest pointing to collected bytes. Hosts remain responsible for
encoding media and for exporting these generic native spans as Ufor recording
fragments and gaps.

## Consumer migration checklist

These five projects should read the relevant sections and adopt the listed APIs:

| Project | Features to adopt |
|---|---|
| Tuney | Atomic output, validated configuration edits, local resource claims |
| Streamo | Atomic output, retry scheduling |
| Recs | Validated configuration edits, event connection completion, local resource claims |
| Showco | Event connection completion |
| Lyte | Retry scheduling |
| Ufor hosts | Verified finite asset store |

The Ufor format library remains free of cache I/O. A host resolving Ufor asset
locations can adopt the verified finite asset store. Showco may also adopt retry
scheduling later, but that is optional and outside the initial migration scope.

## Atomic output

Import `atomic_output` from `reccy.runtime.files`. It creates parent directories
and yields a closed, initially empty temporary file beside the destination,
preserving its suffix. Write through any encoder, closing its handles before
leaving the context. A successful exit replaces the destination; failure leaves
the old destination intact and removes the temporary file.

```python
with atomic_output(destination) as temporary:
    temporary.write_bytes(contents)
```

Concurrent writes are last-replacement-wins. `sync=True` fsyncs the finished file
before replacement; it does not fsync the directory or promise full power-loss
durability. Existing text/JSON settings writers still default to syncing.
This is neither a resource lock nor a multi-file transaction. Callers own content
validation and any special permission policy.

Tuney migration: replace the local `app.file_output.atomic_output` implementation
and its imports with this function. Streamo migration: use it inside image/cursor
publication, retaining image validation. Neither migration is implemented here.

## Validated configuration edits

Import `validated_update` from `reccy.configuration.update`:

```python
updated = validated_update(config, ['timing', 'duration'], '250ms')
```

The result is a new model of the same type, fully revalidated with authored unit
values preserved. The original is not mutated, including on failed validation.
Paths are nonempty sequences of canonical model field names, never dotted strings,
aliases, dictionary keys or list indices. Unknown or excluded fields and traversal
through non-model values raise ValueError. Pydantic validation errors propagate.
The function inherits the unit-dump restrictions on custom serializers/RootModel;
revalidation explicitly uses field names, including for aliased models.

Recs migration: retain the mutable-address allowlist and split the supported
address into components before calling this helper. Tuney migration: use the
returned model for a single-field edit, retaining undo, cache invalidation,
hardware side effects and UI refresh in Tuney. Preset merging is not implemented
by this function. Consumer adoption remains deferred.

## Event connection completion

`rpc.EventClient.wait_closed(timeout)` returns whether the transport has closed.
It returns False before startup or while connected unless the timeout permits
waiting for closure. `terminal_reason` is initially None, then one
`rpc.EventCloseReason`: local_close, peer_eof, protocol_error, callback_error,
transport_error or timeout. The first terminal cause wins and repeated close
does not overwrite it. Startup failures also notify waiters and still raise.

Callback exceptions still reach `threading.excepthook`; there is no reconnect,
command replay or implicit retry. Local close wakes owners after transport cleanup
but does not join a callback already executing. Applications must coordinate their
callback-owned resources separately. Use a fresh EventClient for reconnection.

Showco migration: use completion to wake its waveform reconnect loop, retaining
subscription activation, stop handling and retry policy. Recs watch migration:
stop waiting when the connection ends even without a final status event. Neither
migration is implemented. Initial snapshot/event ordering remains unchanged and
is not an atomic handoff guarantee.

## Local resource claims

Import `ResourceClaim` and `ResourceClaimConflict` from `reccy.runtime.claims`.

```python
with ResourceClaim(lock_path):
    save_settings()
```

Construction does not acquire. `acquire()` is nonblocking and returns the claim;
`release()` is idempotent. Entering a context acquires and exiting releases, even
after an exception. Acquiring the same held object again raises RuntimeError.
Contention raises ResourceClaimConflict; file-open permission and other I/O errors
propagate separately. Parent directories are created; new lock files use mode
0600, subject to platform permissions. Existing contents are neither interpreted
nor changed, so there are no invalid/stale PID records to recover.

POSIX uses flock; Windows locks byte zero with msvcrt's nonblocking lock. Closing
the descriptor releases it; process exit also releases it. Keep the object alive
and explicitly release it. Do not fork while holding claims: inherited descriptors
may retain locks. Methods on one claim object require caller serialization.

The lock file is deliberately never deleted. Every cooperating writer must use
the same stable path on a local filesystem. Never unlink/replace it or use
`atomic_output()` on it: replacing it creates a different lock object on POSIX.
Release cannot remove or unlock a replacement file, but cannot stop another
process replacing the path either. Use an application-owned directory; this is
cooperative ownership, not protection against hostile filesystem changes or a
distributed lock. It does not lock the separate settings/output file itself.

Tuney migration: replace its PID-marker instance claim, retaining GUI messages.
Recs migration: use a dedicated stable lock path for settings ownership, retaining
instance discovery separately. Stop old consumers before migration: PID-file
claims and OS locks do not coordinate. Migrations are deferred. Tests exercise
separate-process contention, exit/crash release, contents preservation and errors
on macOS; native Windows validation remains outstanding.

## Retry scheduling

Import `RetryPolicy`, `RetrySchedule` and `RetryStopReason` from
`reccy.runtime.retry`. The first attempt is immediately eligible. `attempts`
counts total attempts, including the first; None means unlimited. `delay` is the
first failure's wait, `backoff` defaults to 1 (fixed delay), `backoff_after` selects
the failed attempt after which later delays multiply, and `max_delay` caps every
delay (default 60 seconds). Timing must be finite and nonnegative; backoff is at
least 1. Supply a monotonic clock for deterministic tests and an optional absolute
deadline in that same clock domain.

`begin_attempt()` returns True only when an attempt may start, and counts it.
After a retryable failure, call `failed()`. `seconds_until_attempt()` returns a
nonnegative delay, or None when stopped. `stop_reason` distinguishes cancellation,
deadline and exhaustion. After success, `reset()` clears the cycle, including
cancellation, but preserves the absolute deadline. A successful operation returning
None is not confused with failure: the schedule never executes operations.

Only one attempt may be active per schedule; finish it with failed/reset before
asking for another. Methods require caller serialization. `cancel()` blocks future
attempts but does not interrupt an active operation or wake a caller-owned wait.
Consumers using a stop event should use its interruptible wait, then cancel the
schedule when signalled. No sleeping, threads, exception handling or implicit
retries are performed by this API. Operations still need their own timeouts.

Lyte migration: use the schedule inside its synchronous retry loop, keeping
exception selection, logging and stop-event waiting. Explicitly choose a delay cap
when migrating its formerly uncapped backoff. Streamo migration: call
`begin_attempt()` from update loops, use failed/reset for recovery, and retain
device/process ownership locally. Use separate schedules for distinct recovery
policies. Do not migrate hardware safety decisions into Reccy. Neither consumer
migration is implemented here.
