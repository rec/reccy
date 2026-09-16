# Candidate shared Reccy features

## Scope and selection

Source review: 2026-09-16. Reviewed Reccy alongside Recs, Tuney, Lyte,
Showco, Streamo and Ufor. Twitcho is not present in the current checkout;
Showco's current deployment code names Streamo instead. These are proposals,
not implementation commitments or claims that consumers already share identical
requirements. Source locations describe the working trees at review time.

Each candidate has at least two concrete prospective consumers. Prefer a small
extraction with two adopting projects over a general framework. Reccy should own
application infrastructure; Ufor should retain portable formats and mathematical
semantics. No audio engines, lighting renderers, Qt widgets or streaming-provider
logic belong in these extractions.

Suggested order: F01, F02, F03, then F04 and F05 after settling their policies.
All names below are provisional.

## F01. Atomic output for arbitrary file writers

Implemented in Reccy: `reccy.runtime.files.atomic_output(path, sync=False)`.
Creates parent directories, preserves the suffix and closes the initial handle
before yielding. Existing text/JSON writers use it with their sync setting.
Tests cover text/binary writers, failure cleanup, concurrency and fsync ordering.
Native Windows execution remains unverified. Consumer adoption is deferred.

Migration: Tuney can replace its local context manager with a direct import;
Streamo can wrap its byte writer with this context. Keep consumer validation and
permission policy outside it. See `doc/shared-features.md` for the contract.

Consumers and evidence:

- Tuney's [atomic_output](</Users/tom/code/tuney/tuney/app/file_output.py>) yields
  a temporary path and publishes it only after successful writing.
  [Preset writing](</Users/tom/code/tuney/tuney/presets/preset.py>) already uses it
  for TOML output.
- Streamo's [publish_file and write_feed_cursor](</Users/tom/code/streamo/streamo/images.py>)
  independently implement temporary-file replacement for image bytes and cursors.
- Reccy's [settings writer](</Users/tom/code/reccy/reccy/configuration/settings.py>)
  covers strings and Pydantic JSON, but cannot currently wrap an existing encoder
  that needs a filename or writes binary data.

Proposal: a single `atomic_output(path)` context manager yielding a sibling
temporary path. Preserve the destination suffix for format-selecting writers;
close the initial temporary handle before yielding so consumers can reopen it
on Windows. Publish only on successful exit and clean up on all exits. Reuse
this implementation beneath the existing text/JSON writers, retaining their
documented fsync behavior rather than creating another persistence path.

Decide explicitly whether the context manager creates parent directories and
how durability is requested. Do not imply that rename alone guarantees survival
of power loss. Concurrent publication remains last-replacement-wins, not a lock
or compare-and-swap operation.

Keep TOML serialization, image validation, credential permissions and media
encoding in consumers. This is not a multi-file transaction facility.

Acceptance: adopt in Tuney presets and Streamo image/cursor publication; test
writer exceptions, replacement failures, overlapping writes, suffix preservation
and Windows reopening. Verify existing Reccy text/JSON durability behavior.

## F02. Validated edits to Pydantic configuration

Implemented in Reccy: `reccy.configuration.update.validated_update`.
Tests cover nested and direct edits, aliases, cross-field rejection, unchanged
originals and authored units. Consumer adoption remains deferred; integration
instructions are in `doc/shared-features.md`.

Consumers and evidence:

- Recs's [Cfg.set_attr](</Users/tom/code/recs/recs/cfg/cfg.py>) checks a mutable-field
  allowlist, produces a provenance-preserving dump, changes a nested value and
  reconstructs the model.
- Tuney's [_set_model_value and _apply_section_preset](</Users/tom/code/tuney/tuney/ui/control_panel.py>)
  also use `units.revalidation_dump()`, edit values, and call `model_validate()`
  before applying UI-specific changes.
- Reccy already provides unit-preserving dumps and a `set_attr` lifecycle hook,
  but not the common dump/edit/revalidate operation.

Proposal: a pure `validated_update(model, path, value)` returning a new validated
model, leaving the original untouched. Start with field-name path components,
not an ambiguous dotted string, arbitrary expressions or a general JSON Patch
language. Reject unknown fields even if the model normally ignores extra input.
Preserve authored unit values via the existing revalidation helper and inherit
its restrictions on custom serializers and root models.

Recs keeps its mutable-field allowlist and address syntax. Tuney keeps undo,
cached-property invalidation, MIDI sends and UI rebuilding. The helper does not
save settings or mutate live application state. Collection indexing and preset
merge rules should wait until two consumers need the same behavior.

Acceptance: adopt one Recs nested-field edit and one Tuney control edit. Test
cross-field validation, rejected edits leaving the original unchanged, nested
models and authored-unit preservation. Verify alias handling against the chosen
model contract, rather than bypassing validation with `model_copy(update=...)`.

## F03. Observable RPC event-connection lifecycle

Implemented in Reccy: `EventClient.wait_closed()` and `EventCloseReason` exposed
through `terminal_reason`. First terminal cause wins; startup failures notify
owners; callback errors remain thread exceptions. Completion means transport
cleanup, not joining an in-flight callback after local close. Tests cover EOF,
local close, malformed input, callback errors, connection failure and timeout.
Consumer adoption remains deferred; see `doc/shared-features.md`.

Consumers and evidence:

- Showco's [WaveformHub](</Users/tom/code/showco/showco/runtime/waveforms.py>) owns
  a reconnect loop around `rpc.EventClient`, a stop event, subscription setup
  and retry/logging delays.
- Recs's [watch](</Users/tom/code/recs/recs/daemon/watch.py>) opens an event client,
  requests an initial snapshot and waits on a separate completion event.
- Reccy's [EventClient](</Users/tom/code/reccy/reccy/protocol/rpc.py>) closes itself
  on EOF or malformed input, but offers no completion notification or structured
  terminal reason to wake these consumers.

Proposal: expose an event connection's completion through `wait_closed(timeout)`
and a terminal reason distinguishing local close, peer EOF, protocol failure and
callback failure. Set completion on every exit path. Define whether callback
exceptions remain thread exceptions or are also available to the owner; do not
silently absorb them.

Showco can use the notification to drive its existing reconnect loop. Recs watch
can stop waiting when the transport disappears without a final application event.
Keep automatic reconnection out of the first version: the two consumers do not
necessarily want the same retry policy. EventClients remain single-use; reconnect
means constructing a new instance.

Snapshot acquisition and waveform subscription commands remain consumer-owned.
Do not claim an atomic snapshot/event handoff: that would require explicit server
ordering or sequence semantics and a separate protocol decision. Never replay
arbitrary control commands after a connection failure.

Acceptance: adopt in Showco and Recs watch; simulate silent EOF, malformed events,
callback failure and local close. Verify owners wake exactly once and shutdown
does not leave a reader or retry loop running.

## F04. Owned local resource claims

Implemented in Reccy using the approved OS-backed approach:
`reccy.runtime.claims.ResourceClaim`, with ResourceClaimConflict for contention.
Lock files remain in place; no PID records or stale-file reclamation. Tests cover
separate processes and crash recovery on macOS. Native Windows validation and
consumer adoption remain deferred. Stable-path and migration requirements are
documented in `doc/shared-features.md`.

Consumers and evidence:

- Tuney's [acquire_single_instance/release_single_instance](</Users/tom/code/tuney/tuney/app/platform_info.py>)
  use an exclusive-create PID file, process-liveness checks and owner-aware release.
- Recs's [claim_settings/release_settings](</Users/tom/code/recs/recs/daemon/instances.py>)
  separately prevent two processes from saving the same settings file, recording
  instance identity and checking an existing owner's process.
- Reccy's atomic writers protect file completeness, not ownership. This is a
  distinct capability, not another atomic-write wrapper.

Proposal: a local `ResourceClaim` acquired for a caller-supplied path, with
explicit release and context-manager support. Return a useful conflict result
instead of reducing permission errors, corrupt records and live owners to the
same boolean. Release only the acquired claim, never a replacement owner's file.

Before implementing, choose between OS-backed locking and a safely designed
ownership-record protocol. Do not simply move the existing stale-file deletion
loops into Reccy: competing reclaimers, PID reuse and process-start identity need
an explicit contract. Fail closed when ownership cannot be established. Limit the
first version to local filesystems; distributed or network-filesystem locking is
not part of this proposal.

Tuney retains its single-GUI policy and user messages. Recs retains settings-path
identity, instance discovery and the decision about which processes may write.
Do not automatically impose a single-instance policy on every Reccy application.

Acceptance: both consumers adopt the same primitive. Test contention in separate
processes, crash recovery, invalid ownership records, permission failures and
release after ownership replacement; validate on Windows and POSIX.

## F05. Deadline-aware, cancellable retry scheduling

Consumers and evidence:

- Lyte's [retry_call](</Users/tom/code/lyte/lyte/retry.py>) implements attempts,
  backoff, deadlines and stop-event-aware waiting for transient device operations.
- Streamo's [AudioCapture.update](</Users/tom/code/streamo/streamo/audio.py>) and
  [LocalDisplayController](</Users/tom/code/streamo/streamo/streamer.py>) maintain
  `retry_at` timestamps and fixed delays in caller-owned update loops.
- Showco's waveform subscriber also waits between connection attempts. It could
  adopt the same scheduling rules later, but is not needed to justify extraction.

Proposal: start with the shared timing calculation, not a universal supervisor.
A small retry schedule should track attempt count and the next permitted attempt
using an injected monotonic clock, support fixed or bounded exponential delay,
and respect an overall deadline. Successful recovery resets the schedule.

Lyte can drive it from its synchronous retry loop, retaining cancellable waits.
Streamo can query it from `update()` without blocking the audio/display loop.
Add a blocking convenience operation only if it replaces Lyte's existing wrapper
cleanly; do not maintain parallel policy implementations.

Consumers choose retryable exceptions, whether an operation is safe to repeat,
logging and user-visible failure state. Distinguish exhaustion/cancellation from
a successful operation returning None. A deadline limits scheduling, but cannot
interrupt a blocking operation: each operation still needs its own timeout.
No implicit RPC retries, background threads, async framework or network I/O
belongs in the scheduling primitive.

Acceptance: migrate Lyte's retry loop and one Streamo recovery loop with their
current delays preserved. Fake-clock tests cover attempt limits, deadline
clamping, cancellation and recovery reset, without sleeps or hardware.

## Deliberately not proposed

- Another JSON settings writer, basic RPC client, process terminator or CLI-help
  fixture: Reccy already has these. Adoption alone is not a new feature.
- A shared audio/MIDI/lighting execution engine or score model: these belong in
  application runtimes or Ufor, not application infrastructure.
- Generic secret redaction based only on Streamo's FFmpeg implementation: a
  second concrete extraction site was not established in this review.
- One universal device selector: Tuney's duplicate-name/output-device behavior
  is not evidence that all consumers want the same matching policy. Establish
  a second matching contract before extending `reccy.device`.
- A plugin system, database, deployment orchestrator or web framework. The
  inspected duplication does not justify those architectures.

## Delivery rule

Implement one candidate at a time, beginning with two consumer adapters/tests
that demonstrate the same small contract. Make any required architecture or
behavior decisions before extraction. Keep consumer migrations separately scoped
and coordinated with their working agents; this plan authorizes none of them.

## Additional work beyond the prompt

None.
