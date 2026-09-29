# Shared infrastructure features

Consumer migrations are deferred. Implementations and tests here change only
Reccy; no claim is made that sibling projects have adopted these APIs.

## Verified finite asset store

`reccy.runtime.assets.AssetStore` is a private, host-owned filesystem store for
finite opaque bytes. Construct it with a per-user directory and a host-issued
credential scope ID; each scope has separate objects and metadata. Then use
`import_bytes()` to verify SHA-256 and length, publish one immutable object, and
create an immutable acquisition/materialization entry. The entry records a
versioned source key and one of the eight source kinds without treating that key
as proof that two source requests have equal bytes.

```python
store = AssetStore(cache_directory, credential_scope='public')
key = source_fingerprint(
    {'kind': 'relative_file', 'path': 'audio/take.wav'},
    {'package': 'session-42'},
    expected_identity,
    {},
)
entry = store.import_bytes(
    contents,
    source_key=key,
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

`source_fingerprint(location, context, expected, representation)` computes a
versioned key from resolved JSON request facts. It preserves integer/float,
Boolean/null, Unicode, ordering within arrays, and original URL spelling while
ignoring object-key order. Package identity, volume ID, effective provider
arguments, and media representation settings belong in the caller-supplied
facts when relevant. If a secret affects lookup, supply it as `lookup_secret`
with a stable host-private `fingerprint_key` of at least 32 bytes; only a keyed
digest enters the request key. Never place raw credentials, cookies, signed URL
parameters, or secret arguments in the public facts or persisted metadata.
The host resolves credentials and the scope independently; a matching key does
not authorize access to another scope. Stored source keys must be `v1:` followed
by a lowercase SHA-256 digest; raw source descriptions are rejected.

`import_stream()` reads a finite source in chunks into staging, with a required
maximum byte count. An oversized body, interrupted read, or wrong expected
identity never publishes an entry. `import_file()` uses that path to copy a
relative file beneath a host-supplied package or volume root into an independent
snapshot. It rejects traversal, symlinks, and nonregular files. The host still
maps volume IDs to approved roots and authorizes access before calling it.

For a source the host trusts to remain immutable throughout use,
`open_verified_file(root, path, expected, trusted_immutable=True)` verifies and
returns the same open file handle without caching a copy. A mutable source must
use `import_file()` instead. Reccy cannot make in-place changes by another
process impossible after a direct read is verified.

After the host authorizes an immutable asset request, `open_expected(identity)`
finds verified retained bytes in that credential scope without contacting the
original source. It leases the selected entry before reading. A missing entry
raises `AssetCacheMiss`; an existing but corrupt object raises
`AssetCorruptionError`. Content identity is not authorization, so hosts must
check request authority before this lookup.

`git_assets.import_local_git_file()` reads a full-commit, path-selected regular
blob from a host-approved local Git repository into the bounded store. It does
not check out files or run filters, hooks, or lazy remote fetches. Symlinks,
submodules, and unresolved Git LFS pointers are rejected. The returned Git blob
ID is transport evidence and remains distinct from the entry's file SHA-256.
Remote fetching and transport-cache limits remain host work.

`http_assets.open_https_asset()` resolves a declared immutable HTTPS object.
The host authorizes the initial URL and each redirect and supplies credentials
and a private fingerprint key. A retained object in the same credential scope
is opened offline by SHA-256 and length. Otherwise the adapter bounds encoded
and decoded transfer, handles identity and gzip bodies, and checks the declared
content identity before publication. Cross-origin redirects drop Authorization,
Cookie, and Proxy-Authorization headers. `no-store` and `Vary: *` responses are
verified in bounded memory for the current use and are not persisted. HTTP
freshness, conditional validation, and mutable current-URL imports remain
separate future work; this adapter never treats a changed URL body as a score
update.
`reccy.runtime.http_freshness.response_freshness()` calculates explicit
private-cache lifetime and corrected age from response and request headers.
It grants no heuristic or stale reuse. `reccy.runtime.http_current`
uses it for current-URL imports. `open_current_https_asset()` returns the
verified content identity and a leased byte stream. It stores only selected
response metadata under an opaque request key, and conservatively keys all
request headers so differing `Vary` variants cannot collide. Fresh bodies are
reused; stale bodies use ETag or Last-Modified validation when available, or
fetch an unconditional replacement. A 304 with no stored body causes an
unconditional fetch. `no-store` and `Vary: *` bodies are transient. One
acquisition at a time may update each request key, and an incomplete body is
not published.

`RetentionRule` and `RetentionMatch` provide pure, additive rule evaluation for
finite entries. A rule either `protect`s an entry from every collection mode or
`retain`s it until its deadline, which pressure collection may override.
`RetentionNewest(count=N, group_by="source" | "all")` retains the newest N
matching entries in each source group or across all matches. It may be combined
with a duration: either condition retains the entry. Equal creation times are
ordered by entry ID. A newest rule cannot combine with `retain="forever"`,
which would make its ranking ineffective. `explain_retention()` reports each
matching newest rule's rank, including entries outside the retained count.
Download-only rules may use `retain="while_fresh"`. The store reads the scoped
current-URL response metadata and retains an entry for ordinary collection
only while that response is fresh. Pressure collection may evict it, and a
missing or stale response record grants no retention.
`explain_retention()` reports applicable rules and roots, `plan_collection()` is
read-only, and `collect()` rechecks roots under the store-wide metadata claim
before deleting manifests and then unreachable objects. Rules may select an
asset category, source/media kind, source key, or tags. A missing rule leaves an
unrooted entry eligible. URL/Git acquisition, HTTP freshness, providers and
capture streams remain host-specific later stages of Ufor's asset-cache plan.
`inspect_recovery()` reports staged files and objects with no entry manifest,
including byte counts, without deleting them. It labels capture recovery
records separately from raw staging files. A staged file might still have a
live writer, so the report does not classify it as abandoned.
`export_entry()` leases and verifies a finite object before atomically copying
it to a host-approved destination. Package layout and destination authorization
remain with the consuming host.
`reccy.runtime.file_assets` resolves host-measured volume IDs to current roots.
The optional display name is diagnostic only; a different ID never matches by
name. `open_file_asset()` reads a trusted immutable file directly through its
verified handle, or copies a mutable file into the verified store before use.
The host remains responsible for selecting and authorizing package and volume
roots.
`reccy.runtime.value_assets.open_deterministic_asset()` caches a host-declared
finite provider result by an opaque source key. It remembers the first content
identity even after collection. Regeneration with different bytes invalidates
the mapping and reports `AssetValueConflict`; a later call cannot silently
replace it. The host must construct the key from all effective inputs, versions,
and output settings, and encode the provider's result to bytes.
An optional `AssetCapacity` bounds distinct stored-object bytes, total staging
bytes, and the minimum free-space margin for admissions. Imports using a
capacity serialize across cooperating processes and check staging growth before
each write; an exceeded limit or `ENOSPC` raises `AssetInsufficientSpace`.
Every process sharing a scope must use the same capacity. Automatic pressure
collection, Git transport quotas, and capture-recovery reservations remain
separate work.

## Bounded asset capture

`reccy.runtime.capture.CaptureStore` builds immutable finite capture versions on
an `AssetStore`. Every `CaptureSpec` has a maximum byte budget and exactly one
frame limit, duration limit, or manual-stop mode. It also records resolved media
facts and adapter/encoder versions. Callers select captures by immutable ID or a
named reference; pinning a reference snapshots its current capture ID, so moving
the reference cannot retarget the pin.

For callback and client-buffer sources, `CaptureSession.queue_fragment()` copies
borrowed storage into one preallocated bounded byte queue and does no filesystem
I/O. `maximum_queue_bytes` is allocated when the session starts; the host is
trusted to choose that budget, and reccy does not impose an additional cap.
`drain()` performs object publication outside the producer call. Queue
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

Construction does not acquire. By default, `acquire()` is nonblocking and returns the claim;
`release()` is idempotent. Entering a context acquires and exiting releases, even
after an exception. Acquiring the same held object again raises RuntimeError.
Contention raises ResourceClaimConflict; file-open permission and other I/O errors
propagate separately. Parent directories are created; new lock files use mode
0600, subject to platform permissions. Existing contents are neither interpreted
nor changed, so there are no invalid/stale PID records to recover.

The default timeout is zero. `ResourceClaim(path, timeout=seconds)` waits up to a
finite, nonnegative number of seconds for normal contention, then raises
`ResourceClaimConflict`. Asset and capture metadata operations use a five-second
timeout; ordinary instance claims remain nonblocking unless callers opt in.

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
