# Shared infrastructure features

Consumer migrations are deferred. Implementations and tests here change only
Reccy; no claim is made that sibling projects have adopted these APIs.

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
