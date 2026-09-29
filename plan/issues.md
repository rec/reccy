# Project issues and audit notes

Static review of the repository on 2026-09-29. Scope: all tracked source,
tests, project configuration, README, and docs in this checkout. This is an
issue inventory, not an implementation plan or a claim that every failure has
been reproduced. Severity describes likely impact if the relevant API is used;
platform-specific findings need native confirmation. References are repository
relative and line numbers refer to the reviewed revision.

## High priority: data loss, unbounded work, and trust boundaries

1. **A failed capture drain discards queued bytes.** `CaptureSession._take_queue()`
   removes every fragment before `drain()` imports or pins any of them
   (`reccy/runtime/capture.py:368-392,435-446`). Disk full, fsync failure,
   lock contention, corrupt existing objects, or a pin-write failure leaves
   the rest of that batch absent from both the queue and recovery manifest.
   If `add_pin()` fails after import, the new entry is also unrooted. Keep a
   durable/retryable in-flight batch or record a failure without silently
   dropping it; test failures at each persistence step.

2. **Capture finalization is not serialized.** `finish()`, `abort()`, `salvage()`,
   and `drain()` mutate `_fragments`, `_stored_bytes`, `_closed`, and publication
   state outside `_lock` (`reccy/runtime/capture.py:368-433,486-492`). Two
   drains can interleave, and simultaneous finish/abort calls can publish both
   records or return a manifest missing a fragment. Serialize consumer and
   finalization operations, and test concurrent drain/finalize calls.

3. **Asset collection can race with opening or rooting an entry.**
   `open_entry()` reads and verifies before creating its lease under the
   metadata claim (`reccy/runtime/assets.py:321-333`). `set_reference()` and
   `add_pin()` also check entry existence before taking that claim
   (`:344-370`). A collector can delete the manifest and object between those
   steps, leaving a failed open or a reference/pin to an absent entry. Put the
   existence check and lease/root creation in the same critical section as
   collection; test with a controlled interleaving.

4. **Abandoned leases and capture fragment pins can exhaust storage.** Leases
   are plain JSON records removed only on normal context exit
   (`reccy/runtime/assets.py:321-342,494-515`); a killed process leaves a
   permanent collection root. Every drained capture fragment gets a permanent
   asset pin (`reccy/runtime/capture.py:373-391`), but `CaptureStore` has no
   capture deletion, pin removal, or collection operation. Aborted and
   unreferenced captures therefore retain their bytes indefinitely. Define
   ownership/recovery and a collection lifecycle before relying on this for
   long-running capture.

5. **Windows pipe frames are unpickled from the peer.**
   `WindowsPipeConnection.read_lines()` calls `pickle.loads(frame)` on received
   bytes (`reccy/protocol/ipc.py:357-367`). A peer that can connect to the
   pipe can supply a pickle payload with code-execution side effects, before
   RPC message validation. `Listener`/`Client` are constructed without an
   `authkey` (`:333-355,400-403`), and the hello `role` is not verified by
   RPC (`reccy/protocol/rpc.py:385-410`). Use a byte/string framing format
   that does not execute objects. Verify the actual Windows pipe access
   controls as a separate part of the threat assessment.

6. **A timed-out Windows pipe connection can leak its eventual handle.** The
   helper thread continues after `results.get()` times out, and puts a later
   successful `connection.Client()` result into an otherwise abandoned queue
   (`reccy/protocol/ipc.py:392-416`). Repeated intermittent connections can
   consume handles. Close late results and exercise delayed success in a test.

7. **RPC handler work has no deadline after request acceptance.** The server
   cancels its one-second timer immediately before calling `handle()` and each
   handler owns one of only 16 request slots until it returns
   (`reccy/protocol/rpc.py:300-337`). A blocked handler can exhaust all slots;
   `Server.close()` disconnects clients but cannot stop the handlers. Define
   handler cancellation/timeout policy at the application boundary and test
   shutdown with persistent blocked handlers.

8. **Metadata claim contention is a routine hard failure.** `ResourceClaim`
   deliberately takes a nonblocking lock (`reccy/runtime/claims.py:26-47`),
   and asset/capture imports, opens, reference changes, and collection directly
   use it (`reccy/runtime/assets.py`, `reccy/runtime/capture.py`). Two valid
   cooperating processes or threads can raise `ResourceClaimConflict` during
   ordinary activity. In capture, this also triggers issue 1. Establish a
   bounded serialization/wait policy at these store operations and test
   contention, while keeping real lock failures distinguishable.

## Lifecycle, exceptional conditions, and resource limits

9. **RPC close/start are not synchronized with accept threads.** `Server.close()`
   changes `running`, closes backends and current connections, but never joins
   the accept or worker threads (`reccy/protocol/rpc.py:231-299`). Reusing the
   same server after `close()` can leave an old accept loop competing with a
   new one, and a connection accepted around close can escape the snapshot of
   connections to close. Either make the server explicitly single-use or
   serialize and join its lifecycle; test repeated start/close under traffic.

10. **The application accepts RPC before startup is complete, and during
    stopping.** `Reccy.start()` starts the server before publishing status and
    calling `on_started()` (`reccy/reccy.py:132-158`). `close()` calls
    `on_stopping()` while RPC is still accepting and handlers can still run
    (`:160-175`). Commands can observe or mutate partially initialized or
    partly dismantled state. `publish_error()` also updates `_errors` and
    writes status without a shared lock (`:206-232`), so competing publishers
    can leave an older snapshot on disk. Gate request handling on lifecycle
    state and coordinate active handlers and status writers before releasing
    application resources.

11. **Server event publication is synchronous and has no shared ordering.**
    `publish()` walks up to 16 subscribers sequentially, writing with a
    per-connection timeout (`reccy/protocol/rpc.py:260-266`; timeout in
    `reccy/protocol/ipc.py:419-444`). A slow subscriber can delay status or
    shutdown by seconds. Concurrent publishers can deliver different event
    orders to different subscribers because only individual connection writes
    are locked. Decide whether event order is part of the contract, and keep
    slow subscribers off critical lifecycle paths.

12. **Malformed transport input is handled inconsistently.** Unix
    `read_lines()` decodes UTF-8 strictly (`reccy/protocol/ipc.py:299-310`).
    RPC server workers catch the resulting `UnicodeDecodeError` through
    `ValueError`, but `EventClient._read()` catches only `OSError` and
    `ValidationError` (`reccy/protocol/rpc.py:181-208`); it can report
    `peer_eof` after an uncaught decoding error. The generic
    `ProtocolListener` catches only validation errors and has no line-length
    or handshake deadline (`reccy/protocol/ipc.py:104-137`). Add malformed-byte
    and stalled-peer tests for each public listener path.

13. **Generic protocol listeners do not own a complete shutdown.**
    `ProtocolListener.start()` detaches a daemon thread and `read()` has no
    `finally` that closes its connection (`reccy/protocol/ipc.py:104-137`).
    Callers have no join/completion signal and EOF does not release the
    connection here. `ProtocolClient.shutdown()` sends without checking
    handshake or write success (`:209-218`). Clarify ownership and expose a
    reliable close/completion contract if consumers depend on these classes.

14. **A control client timeout is not a reliable failure classification.** The
    timer closes the connection while `_hello()`, write, or response parsing is
    in progress; `Client.call()` remaps only `OSError` when `expired` is set
    (`reccy/protocol/rpc.py:52-86`). EOF can therefore become the ordinary
    `ConnectionError('RPC server closed the connection')`, and malformed
    response JSON can surface as raw `ValidationError`. Check timeout state
    across every exit path and give callers a consistent protocol failure.

15. **Windows pipe oversize input is treated as EOF.** The pipe reader catches
    `OSError` from `recv_bytes(maxlength=...)` and simply returns
    (`reccy/protocol/ipc.py:357-363`). The server does not send its size-limit
    error; `test/test_rpc_limits.py:95-99` asserts this silent behavior. Report
    a bounded malformed-request error when possible and at least distinguish
    limit failures in diagnostics.

16. **Capture limits have misleading failure states.** On queue overflow with
    policy `fail`, `_observed_frames` advances before the exception and no gap
    is recorded (`reccy/runtime/capture.py:332-347`); an abort can report
    frames that are neither stored nor represented as lost. A positive
    `frame_count` with an empty byte buffer is also accepted (`:316-366`).
    Validate nonempty payloads and keep frame accounting consistent for
    rejected fragments. Test recovery manifests after overflow.

17. **A duration-limited capture may not be finishable as `bound` when input
    stops.** `_bound_reached` is set for elapsed duration only when
    `queue_fragment()` is called again (`reccy/runtime/capture.py:324-326,
    394-400,448-451`). A source that goes silent after the deadline must use
    another termination value or call the producer API with a dummy fragment.
    Recheck the clock at finalization and test a silent deadline.

18. **Import staging can leave temporary files on write/fsync failure.**
    `_stage()` enters its cleanup `try/finally` only after writing, flushing,
    fsyncing, and closing the named temporary file
    (`reccy/runtime/assets.py:468-478`). Disk full or an I/O error there can
    strand the file. Move cleanup around the whole staging operation and test
    injected write/fsync errors.

19. **Collection has no crash recovery and repeatedly scans all metadata.**
    `collect()` deletes an entry manifest before deleting its object
    (`reccy/runtime/assets.py:446-451`), so a crash or disk error can leave an
    object with no manifest and no later collection path. For every candidate,
    it rescans all roots and entries (`:432-450,488-515`), creating poor
    scaling as the store grows. Add orphan reconciliation and calculate the
    locked collection snapshot once per pass.

20. **A live capture preallocates the full requested queue budget.**
    `CaptureSession.__init__()` immediately creates a bytearray of
    `maximum_queue_bytes` (`reccy/runtime/capture.py:290-314`). The model only
    requires a positive integer no larger than `maximum_bytes` (`:62-105`),
    so an erroneous or untrusted request can exhaust process memory before
    any bytes arrive. Set an application-level cap or use bounded incremental
    allocation, and test rejection of impractical sizes.

21. **Service manager commands have no timeout and installation is partial on
    failure.** `_run()` passes no timeout (`reccy/services/controller.py:206-218`).
    `install()` writes metadata, log, and definition before manager commands
    (`:35-67`); a failed command or interruption leaves changed local files
    and possibly a changed manager state. Make timeout and recovery behavior
    explicit, and test failure at each installation stage. On Linux,
    `uninstall()` can similarly remove the unit before a failed daemon reload
    (`:83-96`).

22. **Log rotation is only synchronized within one stream object.**
    `RotatingLogStream` has an in-process lock (`reccy/runtime/logging.py:12-55`),
    but separate daemon processes writing one configured log can rotate each
    other's files and lose output. A failed rotate also closes the active file
    before opening the replacement. If multi-process writers are supported,
    coordinate them or use the platform journal; test rotation failure and
    concurrent processes.

23. **Dictionary delta state grows without a limit and aliases mutable input.**
    `Jsonl` stores a previous-value map for every distinct key forever and
    updates it with the same nested dict/list objects returned to callers
    (`reccy/protocol/jsonl.py:17-45`). A long stream of unique IDs grows
    memory indefinitely, and mutating a prior input/output can silently
    change the compressor's baseline. Provide a reset/retirement policy or
    document finite-stream ownership, and copy mutable values if mutation is
    allowed.

24. **Child termination and output capture can wait indefinitely.** After a
    five-second graceful wait, `terminate()` calls `kill()` and then an
    unbounded `wait()` (`reccy/runtime/process.py:92-100`).
    `run_silent()` has no subprocess timeout (`:77-89`), and a blocking
    `capture_stderr()` callback stalls its reader thread (`:125-141`). These
    are meaningful during shutdown or a stuck child. Supply caller-owned
    deadlines and make output-callback blocking behavior explicit in tests.

## User-facing APIs and project structure

25. **`legal_url_path()` does not produce a generally safe URL path.** It
    returns a `Path` after replacing a selected set of characters
    (`reccy/paths.py:24-26`), but preserves raw `#` and `%`, which alter or
    invalidate URL interpretation, and other URL component characters with
    special meaning. `legal_filename()` also does not cover Windows reserved
    names or trailing dots/spaces (`:6-20`). Either narrow the names/docs to
    the exact transform or use URL component percent-encoding where a URL is
    actually built. Add examples for `#`, `%`, reserved names and collisions.

26. **Service endpoint metadata can disagree with the actual service.**
    `ServiceController.install()` accepts arbitrary `DaemonMetadata` without
    checking its platform, module, or endpoint fields against its own spec and
    paths (`reccy/services/controller.py:35-67`). The generated runner ignores
    that metadata, while clients can discover endpoints from it (`README.md`,
    service installation contract). A wrong metadata object can install a
    running service that clients cannot reach. Validate the relationship at
    installation or make endpoint ownership explicit.

27. **Several API names and result shapes invite misuse.** `ServiceController`
    `install/start/stop()` return `StatusResult(installed=True/False)` without
    observing running state (`reccy/services/controller.py:35-147`), while
    `status()` does observe it; callers need to remember that a successful
    manager command is not a healthy daemon. `ProtocolClient.shutdown()` means
    peer shutdown, whereas `close()` means local disconnect
    (`reccy/protocol/ipc.py:202-218`). `Jsonl` is a dictionary delta codec,
    not JSONL framing (`reccy/protocol/jsonl.py:1-17`). The README explains
    these distinctions, but the types and method names still permit mistaken
    use; prefer explicit result types/names when next changing those APIs.

28. **The porting guide's old `reccy.paths` mapping is now ambiguous.**
    `doc/porting.md` says the old `reccy.paths` moved to
    `reccy.services.paths`, while a current top-level `reccy/paths.py` provides
    filename and URL path helpers. The document should distinguish the old
    service-path API from the new filename-path API.

29. **Large files concentrate unrelated responsibilities.**
    `reccy/runtime/assets.py` is 594 lines of models, storage, retention policy,
    and collection; `reccy/protocol/ipc.py` is 444 lines of protocol handling
    plus Unix and Windows transports; `reccy/services/controller.py` is 356
    lines of three platform managers and status display. Their size makes
    concurrency invariants and platform behavior hard to review. Split only
    along ownership boundaries during a related fix; avoid a standalone
    repository-wide reshuffle.

30. **Some one-purpose files may not justify separate modules.**
    `reccy/errors.py` (2 lines), `reccy/services/spec.py` (11), and
    `reccy/services/runner.py` (16) are small. `runner.py` is an executable
    module and should stay separate; `spec.py` and `errors.py` are candidates
    for inlining only if their import paths are not already a useful public
    contract. Size alone is not a reason to move them.

31. **Tests are concentrated on success paths and mocks in risky areas.**
    `test/test_assets.py` and `test/test_capture.py` cover ordinary import,
    retention, and capture but no injected I/O failure, concurrent collection,
    crash restart, or persistent capture cleanup. Windows pipe and scheduled
    task tests mostly mock platform APIs; native Windows execution is not
    verified here. RPC tests cover socket shutdown, malformed JSON, limits,
    and timeouts, but not invalid UTF-8, repeated server lifecycle, blocked
    handlers, or publish ordering. The highest value new tests correspond to
    issues 1-10 and 12-18.

32. **A few tests duplicate shape more than behavior.**
    `test/test_atomic_output.py` and `test/test_settings.py` both test atomic
    replacement/concurrent writes; the latter is valuable for integration but
    could focus on settings-specific behavior. Service fixtures and status
    command expectations repeat across `test/test_service_controller.py` and
    `test/test_service_renderers.py`. Consolidate shared setup only if those
    tests need significant edits; keep distinct behavior checks.

33. **Common operational failures escape the user-facing CLI wrapper.**
    `run_main()` formats only Pydantic `ValidationError` and `ReccyError`
    (`reccy/cli.py:27-37`). Service manager failures, missing executables,
    file permissions, broken sockets, and disk-full errors can propagate as
    raw exceptions from the service, process, or RPC APIs. Decide which of
    these are expected user errors, translate them at the relevant public
    boundary, and test the resulting message and exit status.

## Additional work beyond the prompt

None.
