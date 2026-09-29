# Open project issues

Updated 2026-09-29 after the fixes through commit `059185b`. The numbers retain
their original audit identities; gaps are issues already resolved and removed
from this list. These are static-review findings, not all reproduced failures.
Platform-specific behavior still needs native verification.

## Decisions needed

1. **Capture queue memory limit (20).** A session allocates all
   `maximum_queue_bytes` on creation (`reccy/runtime/capture.py`). What is a
   reasonable maximum per capture, and should the application reject larger
   requests? That preserves the allocation-free producer path. Incremental
   allocation would avoid the up-front cost but change that real-time contract.

2. **RPC work and shutdown policy (7, 9, 10, 11, 13).** A handler that blocks
   after accepting a request can occupy one of 16 slots indefinitely, and
   closing the server does not stop active handlers. The server is now
   single-use, new requests are gated during startup/stopping, event order is
   serialized, and generic listeners close on EOF. Remaining choices are
   whether handlers must have application-owned deadlines/cooperative
   cancellation, whether shutdown waits for them, and whether slow event
   subscribers may delay publication or must be isolated. Generic listener
   threads also lack a join/completion contract, and
   `ProtocolClient.shutdown()` does not confirm handshake or write success.
   These choices affect when application resources may safely be released.

3. **Service installation failure policy (21).** Manager commands now time
   out, but installation changes local files and then makes several OS manager
   calls. A failure or interrupt can leave a partial installation; Linux
   uninstall can remove the unit before a failed reload. Should a failure
   restore prior local files and attempt manager rollback, or leave the
   partial state for an explicit retry/repair command? Manager rollback cannot
   be guaranteed atomic.

4. **Log ownership (22).** Rotation is safe only for one stream object in one
   process (`reccy/runtime/logging.py`). Can multiple daemon processes write
   the same log path? If yes, rotation needs cross-process coordination or a
   platform logging facility. If no, document and enforce single-writer
   ownership. A failed rotation can also leave the active stream closed.

5. **Delta-codec lifetime (23).** `Jsonl` now copies mutable values and
   documents finite-stream ownership, but it retains state for every distinct
   key until the instance is discarded (`reccy/protocol/jsonl.py`). Are streams
   guaranteed to be finite and bounded in distinct keys, or should the format
   gain an explicit key-retirement/reset operation? Eviction without a format
   rule would corrupt later deltas.

6. **Portable filename policy (25).** `legal_url_path()` is intentionally a
   readable, lossy name transform, not URL encoding; callers handle collisions
   (for example with `_1`, `_2`). URL delimiters, controls, and Windows
   reserved basenames are now replaced. `legal_filename()` still preserves
   trailing spaces and dots, which Windows does not handle reliably. Should it
   replace them for cross-platform safety? Doing so before the URL transform
   would conflict with the specified rule that removes spaces adjacent to
   `+`, `-`, `_`, or `/`, so the two helpers would need distinct handling.

7. **Public API naming and result semantics (27).** Service `install/start/stop`
   return `StatusResult.installed` without proving the daemon is running;
   `status()` performs that observation. `ProtocolClient.shutdown()` requests
   peer shutdown whereas `close()` disconnects locally, and `Jsonl` is a delta
   codec rather than JSONL framing. Should these names/results be changed now
   for clarity? Backward compatibility is not required, but consumers would
   need coordinated updates.

## Agreed policy, still requiring later work

- **Retain crash artifacts (4, 19).** Abandoned leases, fragment pins, and
  orphan objects can grow storage. Per the current decision, retain all crash
  artifacts; a future crash analyzer will define safe garbage collection.
  Collection no longer rescans all metadata for each candidate, but a crash
  between deleting an entry manifest and its object can still leave an orphan.

## Engineering and verification still open

- **Capture pin failure (1).** A failed import/drain preserves queued bytes for
  retry, but `import_bytes()` can succeed before `add_pin()` fails. That entry
  remains unrooted and retry may create a second entry. Make import-and-pin
  atomic or retain the imported entry through a retry; test pin-write failure.
- **Malformed IPC and Windows pipe behavior (12, 15).** Generic listeners now
  bound message size, close on malformed bytes, and close on EOF. A stalled
  generic-listener handshake still has no deadline. Oversize Windows pipe
  input is distinguished from EOF in code, but whether a protocol error can
  be delivered and the pipe security boundary need native Windows tests.
- **Blocking stderr callbacks (24).** Process kill waits are bounded and
  `run_silent()` accepts a caller deadline, but a blocking `capture_stderr()`
  callback can still stall its reader. Define callback ownership and test it.
- **Service module metadata (26).** Installation now checks metadata platform
  and endpoints against the controller. The module name is caller-supplied and
  may still be invalid; validate it at the public boundary or explicitly
  document caller ownership.
- **Remaining test gaps (31).** Add crash-restart and pin-write-failure cases,
  persistent blocked-handler shutdown cases, and native Windows pipe and
  scheduled-task coverage. Existing tests cannot establish native Windows
  behavior from this macOS checkout.
- **Structure and test maintenance (29, 30, 32).** `assets.py`, `ipc.py`, and
  `controller.py` remain large; `errors.py` and `services/spec.py` are small;
  some service fixtures and atomic-write tests overlap. Split, inline, or
  consolidate only alongside a related change that benefits from it. The
  executable `services/runner.py` should remain separate.

## Additional work beyond the prompt

None.
