# Reccy issues

Reviewed 2026-09-16 against `adec12f` (`Add CLI help Pytest fixture`).
This is a source review of the library, tests, README, and documentation, not a
runtime or platform certification. No applications, services, or tests were run.
References are repository-relative paths and line numbers at that revision.

P1 means potential data loss, hangs, or a broken core path. P2 means a narrower
correctness or usability problem. P3 means naming, documentation, or an unresolved
design choice. Proposed directions and verification cases below are follow-up
work, not implemented fixes. Design concerns are explicitly labeled.

## Transport and RPC

### R01 [P1] Closing Unix connections does not close their file readers

Resolved: Unix connections now shut down both directions before closing the
reader and socket. Local socket-pair tests cover waking a reader, peer EOF,
descriptor release, repeated close, resuming an iterator after close, and an RPC
response deadline against a silent peer.

Evidence: `reccy/protocol/ipc.py:265-292`; timeout callers in
`reccy/protocol/rpc.py:50-88,195-205,230-240`.

`UnixSocketConnection` owns both a socket and its `makefile()` reader, but `close()`
only closes the socket object. The reader retains the underlying descriptor.
Consequently, a timer calling `close()` need not interrupt the blocked read, and
RPC calls or handshakes can hang past their advertised timeout. Subscription
threads can also remain blocked after shutdown. This follows both the installed
CPython socket implementation and the [Python socket close contract](https://docs.python.org/3/library/socket.html#socket.socket.close).

Follow-up: define connection shutdown that wakes blocked readers and releases
both resources. Verify with actual local sockets and silent peers; the timeout
test in `test/test_ipc.py:97-106` uses a fake whose `close()` explicitly wakes its
reader, masking the real transport problem.

### R02 [P1] Stale-socket cleanup can delete an ordinary file or a live endpoint

Resolved: cleanup refuses non-socket paths (including symlinks), and only unlinks
after connection refusal. Tests cover ordinary files, symlinks, stale/listening
sockets, permission failures, and timeouts.

Evidence: `reccy/protocol/ipc.py:345-353`.

Every failed connection attempt is treated as proof that the path is stale, and
the path is unlinked without checking its file type or the error. An ordinary
file at the configured endpoint is deleted. A permission failure or timeout is
also not proof that a socket is unused.

Follow-up: distinguish stale sockets from other files and other connection
failures. Verify that regular files survive and that only the intended stale
socket case is removed.

### R03 [P1] Request bounds stop at the handshake or apply too late

Resolved: RPC asks transports for bounded reads, including hello. The existing
one-second deadline now covers hello plus the first request/subscription, and
event connections (including handshakes) are capped at 16. Unix limits count
UTF-8 bytes including the newline; Windows limits count the serialized frame
before unpickling, preserving the existing wire format. Tests cover oversized
unterminated hello/request messages on both endpoints, post-hello idleness,
subscription capacity, and bounded pipe frame reads. Native Windows pipe behavior
still needs platform validation; pipe framing tests ran through the portable
`multiprocessing.Connection` API.

Evidence: `reccy/protocol/rpc.py:172-259`; `reccy/protocol/ipc.py:278-279`.

The handshake timer is cancelled immediately after hello. A client can then hold
one of the 16 control slots indefinitely without sending a request. The 64 KiB
limit is checked only after an entire line has been read, so it does not bound
memory consumed by a huge unterminated line. Hello messages are not size-limited.
Event connections have no concurrency cap, and post-hello idle subscribers can
accumulate threads. These gaps remain even after R01 is fixed.

Follow-up: bound framing while reading and define deadlines through receipt of
the request/subscription, plus an explicit subscription capacity. Verify idle
post-hello clients, unterminated oversized input, and capacity exhaustion.

### R04 [P1] A slow event subscriber can block the publisher

Resolved: each transport serializes complete writes and disconnects on send or
write-lock timeout (0.2 seconds each). Failed event writes already remove the
subscriber. Tests use a non-reading socket peer and concurrent long messages.
The Windows path uses the same deadline helper and the existing pipe close
operation; native Windows overlapped-write cancellation remains platform validation.

Evidence: `reccy/protocol/rpc.py:164-170`;
`reccy/protocol/ipc.py:258,273-286,331-336`.

Publishing writes synchronously to each subscriber. Unix connections use blocking
`sendall()` without a write deadline; a subscriber that stops reading can stall
status publication, error reporting, and `Reccy.close()`. Concurrent publishers
also write to the same connections without a per-connection serialization rule;
the list lock protects membership only.

Follow-up: define bounded send behavior and serialize complete messages per
connection. Verify a non-reading subscriber and simultaneous publishers.

### R05 [P2] Server shutdown does not own all accepted connections

Resolved: the server registers accepted connections before dispatch, closes all
of them on shutdown, prevents new dispatch/subscription registration once closed,
and rolls back partial startup. Already executing application handlers may finish;
Python threads are not forcibly interrupted. Tests cover failed event-backend
startup, post-hello control/event clients, and an active handler during shutdown.

Evidence: `reccy/protocol/rpc.py:144-162,172-259`.

`close()` closes listeners and registered event subscribers, but does not track
active control connections or event connections still negotiating. Workers can
continue calling application handlers after shutdown. If starting the event
backend fails, the already-started control backend is not rolled back.

Follow-up: make ownership cover startup failures and all accepted connections.
Verify failure of the second backend and shutdown during a request or handshake.

### R06 [P2] Client lifecycle and handshake contracts are inconsistent

Resolved: IPC clients expose local `close()`, track hello completion, require it
before forwarding application data, and release connections on reader exits or
startup failure. RPC request timeouts now include hello; event subscription
startup has a one-second deadline and cleanup on failure. Event reader failures
also close the transport. Client instances are single-use after connecting, so
restart cannot race an old reader's cleanup. Tests cover premature application
data, failed hello sends, real-socket handshake timeouts, malformed hello/events,
and an event callback exception.

Evidence: `reccy/protocol/ipc.py:184-233`;
`reccy/protocol/rpc.py:50-88,105-123`;
`test/test_ipc.py:219-240`.

`ProtocolClient.closed` is set on several exits without actually closing the
connection, and the class has no public `close()`. It accepts application data
before receiving hello, unlike `ProtocolListener`; the test explicitly exercises
this. `EventClient.start()` performs a synchronous handshake with no deadline,
does not clean up failed startup, and its reader exits on malformed messages or
callback errors without a cleanup path. RPC timeout conversion also begins after
`_hello()`, so handshake expiration can surface as a different exception.

Follow-up: specify when each client becomes ready, what `closed` means, and who
releases resources on every exit. Verify missing hello, malformed events, failed
subscribe writes, and failed startup cleanup.

### R07 [P2] Transport selection depends on Python type, not endpoint meaning

Resolved: local named-pipe addresses select Windows pipes and all other endpoints
select filesystem sockets, consistently for `Path` and `str`. Tests cover both
representations and an RPC call using a JSON-round-tripped metadata endpoint.
README documents the selection and client lifecycle contracts.

Evidence: `reccy/protocol/ipc.py:58-67`;
`reccy/services/models.py:54-60`; `reccy/services/renderers.py:17-22`.

`Path('/tmp/app.sock')` selects Unix sockets, while the identical endpoint as a
string selects Windows pipes. Metadata serializes both endpoint kinds to strings,
so passing `metadata.control_endpoint` directly to `rpc.Client` selects the wrong
transport on Unix. The public `Path | str` annotation does not explain this trap.

Follow-up: document and consistently restore the endpoint kind at the metadata
boundary, or choose an unambiguous endpoint representation. Verify a metadata
round trip into a client.

### R08 [P2] RPC success dictionaries can be mistaken for protocol errors

Resolved by contract: top-level `type="error"` is reserved for protocol errors,
which require a string `message`. Successful results must avoid this discriminator.
README documents this restriction; the existing raw wire format is unchanged.

Evidence: `reccy/protocol/rpc.py:26,72-76`;
`reccy/protocol/ipc.py:53-55`.

The server allows arbitrary dictionary results, but the client first validates
every response as `ipc.Error`. A legitimate result such as
`{'type': 'error', 'message': 'last recorded error', 'count': 3}` raises
`ConnectionError` instead of being returned. The shape is effectively reserved
without being documented or excluded from `Result`.

Follow-up: settle the reserved-result contract and test a successful application
dictionary with those keys before changing the wire format.

## Persistence and application lifecycle

### R09 [P1] Concurrent atomic writes share the same temporary file

Resolved: JSON and service metadata writers share a unique-temporary-file writer.
Concurrent publication is last-replacement-wins, without mixing contents. Failed
writes clean up their temporary file. Tests force overlapping replacements.

Evidence: `reccy/configuration/settings.py:30-40`;
`reccy/services/controller.py:352-358`.

All writers to a destination use `.<name>.tmp`. Two writers can truncate or
overwrite the same temporary file; one rename can leave the other writing into
the already-published destination, and the second rename can fail because the
temporary pathname is gone. Atomic replacement alone does not make this safe.
Concurrent RPC handlers make overlapping status/settings writes plausible.

Follow-up: give each write its own temporary file and define concurrent-write
semantics. Verify interleaved writes, not only the sequential round trips currently
covered by tests. Apply the same correction to both implementations.

### R10 [P1] Shutdown can permanently skip RPC cleanup after a status-write error

Resolved: failed startup and shutdown always release RPC ownership and call
`on_closed()`. Repeated starts raise; repeated closes are harmless. Failure-hook
and publication tests verify cleanup. Application-owned resources should be
released in `on_closed()`, including after partial startup.

Evidence: `reccy/reccy.py:123-147`.

`close()` clears `_started` before saving status and publishing events. If either
step raises, the RPC server is not closed; a second `close()` immediately returns
because `_started` is already false. A disk-full status write is a concrete
trigger. An exception in `on_stopping()` also prevents cleanup. Startup failures
after allocating resources have no rollback, and repeated `start()` replaces
the stored server reference before starting the replacement.

Follow-up: make resource cleanup independent of status publication and hook
success, and define repeated-start behavior. Verify failing hooks, failed saves,
and repeated lifecycle calls with controlled fakes.

### R11 [P2] Reccy's status format is incompatible with its default controller

Resolved: controller readers and the default snapshot use `status_model` (or
`ReccyStatus` when unspecified). Subclasses with required additional fields must
override `status_snapshot()` to provide those values.

Evidence: `reccy/reccy.py:17-25,95-98,178-192`;
`reccy/services/models.py:63-69`; `reccy/services/controller.py:22,216-222`.

Reccy writes `errors: list[ErrorRecord]`, but `service_controller()` constructs a
controller whose default `DaemonStatus` expects `errors: list[str]`. Once an error
is recorded, parsing can fail and the controller silently returns no health.
Even without errors, custom status fields are not read using `self.status_model`.
Separately, that class variable only gates publication; setting it does not make
the base `status_snapshot()` construct the chosen model.

Follow-up: establish one explicit relationship between the published status type,
the snapshot hook, and the controller's reader. Verify status through
`Reccy.service_status()` before and after recording an error.

### R12 [P2] Error history grows forever and makes repeated publication quadratic

Resolved: retain the latest 1,000 errors in status and log every error. A test
publishes beyond the limit and checks both retained order and complete logging.

Evidence: `reccy/reccy.py:178-201`.

Every error is retained for the lifetime of the application. Each new error
copies the entire history, serializes it, saves it with an fsync, and may broadcast
it. A recurring device failure therefore causes unbounded memory and status size,
with cumulative copying and serialization growing quadratically in error count.

Follow-up: decide what bounded recent history belongs in status, with complete
history in logs if required. Verify repeated errors against that retention policy.

## Services and platform behavior

### R13 [P1] Windows RPC event endpoints fall back to Unix paths

Resolved: Windows service event pipes append `-events` to the control pipe;
applications without a service spec use `\\.\pipe\<name>` and its event companion.
Both endpoint/backend selections are tested. Native Windows validation remains
outside this macOS run.

Evidence: `reccy/services/paths.py:29-39`; `reccy/reccy.py:72-81,125-132`;
`reccy/protocol/ipc.py:58-61`.

Windows service paths supply a named pipe for control but no event endpoint.
`Reccy.event_endpoint` falls back to a filesystem `Path`, which selects the Unix
socket backend. Applications without a service spec also default to Unix paths
regardless of their `platform`. Thus the shared lifecycle does not provide a
consistent pair of Windows pipe endpoints, despite its cross-platform API.

Follow-up: define both Windows endpoints and test endpoint/backend selection for
applications with and without a service spec. Native Windows validation is still
needed after correcting the source-level mismatch.

### R14 [P2] Windows installation reports a running task without starting it

Evidence: `reccy/services/controller.py:44-61,288-301`.

Installation registers an at-logon scheduled task and immediately returns
`running=True`. It never issues the start command used by `start()`. Linux
installation explicitly starts the service. More generally, lifecycle methods
return assumed states instead of observed status, and uninstall ignores manager
command failures before reporting success and deleting its local records.

Follow-up: distinguish a requested operation from observed state and make install
semantics consistent. Verify the Windows install command sequence and failed
uninstall behavior. Existing controller fakes do not enforce `check=True` errors.

### R15 [P2] Linux uninstall reloads systemd before deleting the unit

Evidence: `reccy/services/controller.py:79-92`.

The controller stops, disables, and reloads the manager while the unit definition
still exists, then deletes it without a subsequent reload. The manager has not
been told to discard the deleted definition.

Follow-up: reload after removal. Verify operation ordering as well as the final
absence of local files.

### R16 [P2] XDG autostart uses shell quoting for a different command grammar

Evidence: `reccy/services/renderers.py:74-94`.

`shlex.join()` produces shell quoting, including single quotes around arguments
with spaces, and does not escape literal percent signs. Desktop `Exec` values
require their own quoting and field-code escaping. Arguments such as `Main Rig`
or a literal `%f` therefore need different rendering. This is a source-level
comparison with the [Desktop Entry Exec specification](https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html),
not a tested desktop-launch result.

Follow-up: implement the target grammar and test spaces, quotes, backslashes,
dollar signs, and percent signs. Separately audit systemd's use of the same shell
quoting approach before assuming it is correct there.

### R17 [P2] Service identity validation does not protect generated paths/text

Evidence: `reccy/services/models.py:17-23`; `reccy/services/paths.py:23`;
`reccy/services/renderers.py:56,86-87`.

`launchd_label` is only checked for non-emptiness and then becomes part of a path.
An absolute label or one containing `../` can move the definition outside
`Library/LaunchAgents`, including during uninstall. Description/display-name
values can contain newlines and are inserted directly into line-oriented service
definitions. These are configuration-author errors the current validation accepts.

Follow-up: validate the values against their actual output context. Verify path
separators, absolute labels, and embedded newlines.

### R18 [P2] File logging can silently be skipped and the replacement stream is fragile

Evidence: `reccy/runtime/logging.py:10-37,40-66`;
`reccy/services/runner.py:8-12`.

`configure(path=...)` returns immediately whenever the root logger already has a
handler, so an earlier console setup prevents the requested file redirection.
It still changes the root level before returning. The replacement stdout/stderr
object supports only `write` and `flush`, so code expecting normal stream
attributes such as `isatty()` or `encoding` can fail. Rotation has no lock across
its close/rename/reopen sequence, even though logging and ordinary prints share
the stream; concurrent writes can encounter a closed file.

Follow-up: specify repeated configuration behavior and the required stream
interface, and coordinate rotation with writes. Verify an existing handler,
terminal-capability queries, and concurrent print/log output near rotation.

### R19 [P2] Status-printing defaults retain the original stdout and stderr

Evidence: `reccy/services/controller.py:259-285`.

Default arguments bind `sys.stdout` and `sys.stderr` when the module is imported.
Later stream redirection, including Reccy's file logging and Pytest capture, is
ignored by callers that omit the explicit output arguments. Output can bypass
the selected log destination or capture stream.

Follow-up: resolve default streams at call time. Verify printing after redirecting
stdout/stderr following import.

### R20 [P2, design gap] Service metadata and platform configuration have split ownership

Evidence: `reccy/services/models.py:54-60`; `reccy/services/renderers.py:11-23,112-125`;
`reccy/services/runner.py:8-12`; `reccy/services/paths.py:29-39`.

Metadata stores a version, platform, and endpoints, but the runner receives only
log path, service name, module, and argv. No library path restores endpoints from
metadata or checks its version. Renderers use live `sys.executable` and, on macOS
and Windows, live `Path.home()`, even when service paths were built for an explicit
home. Windows APPDATA variables also override that home. A caller cannot tell
which values govern installation versus runtime.

Follow-up: document which fields are descriptive and which are authoritative,
then resolve the mismatches within that contract. Include the related frozen-app
gap: runner arguments always use `sys.executable -m`, whereas
`runtime.subprocess.app_command()` explicitly treats a frozen executable differently.

## Configuration, serialization, and process helpers

### R21 [P2] Clock syntax rejects valid fractional seconds above 59

Evidence: `reccy/configuration/units.py:99-112`.

With a preceding minutes component, seconds are rejected when greater than 59.
`0:59.5` is therefore rejected although it is below the next minute. The boundary
should distinguish 59.5 from 60 if fractional seconds are supported, as the float
parser suggests.

Follow-up: test `0:59`, `0:59.5`, and `0:60`, including hour-bearing forms.

### R22 [P2] Unit-aware dump helpers assume serialization preserves model structure

Evidence: `reccy/configuration/units.py:26-48,133-155`.

The helpers walk a normal model dump using original field names and original list
indices. Serialization aliases can prevent authored units from being restored;
a field serializer that filters a list can produce an IndexError or pair values
with the wrong originals. A `RootModel` or model serializer that produces a
non-dictionary fails the unconditional dictionary assertion. The public BaseModel
parameter does not state these restrictions.

Follow-up: define supported model serialization behavior, then cover aliases,
custom list serialization, and root models with explicit cases.

### R23 [P2] Provenance paths are ambiguous for dictionary keys containing dots

Evidence: `reccy/configuration/units.py:115-130,159-160`.

Paths concatenate components with a dot and stringify dictionary keys. A key
`'a.b'` collides with nested keys `'a'` then `'b'`; integer `1` and string `'1'`
also collide. Later provenance overwrites earlier entries in the result.

Follow-up: specify a reversible path representation or restrict supported keys.
Verify both collision cases.

### R24 [P2] Named Tyro values do not serialize to accepted CLI input

Evidence: `reccy/configuration/tyro.py:42-55`; `test/test_helpers.py:49-56`.

`prefix_spec({'fast': 10}, 'SPEED')` accepts `fast`, but formats the corresponding
value as `10`, which its own parser rejects. The test currently asserts this
non-round-tripping behavior. The name also suggests prefix matching, although
the implementation performs exact dictionary lookup.

Follow-up: choose a named-choice contract, define ambiguity when multiple names
map to one value, and test formatting followed by parsing.

### R25 [P2] Numeric validators accept NaN

Evidence: `reccy/configuration/validators.py:39-48`.

Both numeric validators use only comparisons with zero. NaN passes both checks
because those comparisons are false, despite being neither positive nor
non-negative. The unit annotations reject non-finite values, but these standalone
validators do not share that protection.

Follow-up: reject NaN explicitly and decide whether positive infinity is allowed.
Verify NaN separately from negative numbers and zero.

### R26 [P2, contract gap] JSONL compression loses the distinction between absent and null

Evidence: `reccy/protocol/jsonl.py:9-32`; `test/test_jsonl.py:4-24`.

A previously unseen field with value `None` is omitted because `prev.get(k)` also
returns `None`. A deleted field is instead emitted as null and retained forever
by decompression. Thus `Decompress(Compress(records))` does not reconstruct the
original dictionaries. The existing test intentionally preserves part of this
behavior, so changing it is a format decision, not an incidental cleanup.

Follow-up: document whether absent and null are equivalent in this protocol, and
test initial null fields as well as deleted fields. Also state that codec state
persists across calls; reusing a compressor for a fresh output stream can omit
values that a fresh decompressor needs.

### R27 [P2] Output-tail capture can stop draining a child pipe

Evidence: `reccy/runtime/process.py:48-63,114-124`.

An exception from `on_line` terminates the only reader thread. A continuing child
can then fill stderr and block. The tail is bounded by line count only, while the
reader waits for complete lines, so a huge line or output without a newline can
still consume unbounded memory. There is no completion handle for callers that
need the final tail after child exit.

Follow-up: define callback failure and capture completion behavior, and decide
whether a byte bound is required. Verify a failing callback and a long line.

### R28 [P2] Subprocess wrapper advertises text output even in binary mode

Evidence: `reccy/runtime/subprocess.py:12-28`.

`text=False` is accepted, but the return annotation is always
`CompletedProcess[str]`. Callers following the annotation can apply string
operations to bytes. `capture_output=False` also means the captured streams are
not populated, which should be clear in the wrapper's contract.

Follow-up: make the output type reflect the supported modes, or narrow the API
to the mode actually intended for consumers.

## CLI help fixture and test coverage

### R29 [P2] The help fixture includes earlier captured output

Evidence: `reccy/pytest_plugin.py:39-79`.

The first capture drain occurs after invoking help. Output already buffered in
the test is prepended to the help snapshot; earlier stderr can fail an otherwise
successful help invocation. On a nonzero exit, capture is not drained at all.

Follow-up: define an invocation-local capture boundary. Verify prior stdout and
stderr independently from actual help output, including failures.

### R30 [P2, user trap] Normal successful entry-point exits are rejected

Evidence: `reccy/pytest_plugin.py:12,29,65-71`;
`doc/testing-cli-help.md:26-29,62-67`.

An entry point that prints help then returns normally has result `None`;
`SystemExit()` also carries `None`. Both fail `result != 0`, even though they are
normal successful console-program exit paths. This strictness is documented, so
it is an API policy to reconsider rather than an undocumented implementation bug.

Follow-up: decide whether the fixture should accept standard successful exit
semantics, and test normal return, both successful SystemExit forms, and failure.

### R31 [P2, coverage gap] The regression fixture is tested only with a recording fake

Evidence: `test/test_cli_help.py:14-23,26-73`; `pyproject.toml:20-26`;
`doc/testing-cli-help.md:71-76`.

The local `file_regression` replacement records a string; no test exercises actual
baseline naming, creation, or mismatch detection with pytest-regressions. The
docs suggest separate invocations for nested groups without specifying separate
tests/baseline names. Multiple checks in one test would target the same default
baseline, and a multiword program label alone does not create separate argv
tokens for a nested command.

Follow-up: verify a real consumer test with pytest-regressions, and document one
check per test plus a concrete nested-command adapter. Existing tests also omit
nonzero exits, normalization of both bullet variants, and environment behavior.

### R32 [P2] Tests use fixed global temporary paths

Evidence: `test/test_ipc.py:271-300`; `test/test_reccy.py:78-103`.

RPC tests share fixed `/tmp/reccy-rpc-*.sock` paths, and lifecycle tests write to
fixed `/tmp/reccy-test` and `/tmp/reccy-mutable` directories. Concurrent test runs
can collide or observe each other's state. The first lifecycle test accepts
`tmp_path` but ignores it. Unix-specific runtime tests have no platform guard,
despite Windows support being advertised.

Follow-up: isolate filesystem resources per test while respecting Unix socket
path-length limits, and explicitly separate platform-specific tests. Verify
concurrent test runs and the intended Windows test selection.

## Names and incomplete API ideas

### R33 [P3, design] Several names hide the actual operation or scope

These are candidate API improvements, not authorization for a naming sweep:

- `reccy.reccy.Reccy` (`reccy/reccy.py:33`) names the project, not its role as an
  application/daemon lifecycle base. It combines settings, status, RPC, and service
  control with mostly undocumented hooks. Explain its intended minimum subclass
  before choosing a more descriptive name.
- `cli_help` (`reccy/pytest_plugin.py:22`) sounds like help capture but performs a
  regression assertion and returns nothing. A name such as
  `cli_help_regression` would expose that side effect.
- `Jsonl`, `Compress`, and `Decompress` (`reccy/protocol/jsonl.py:4-32`) operate on
  stateful dictionary deltas, not JSON strings or line framing. Names/documentation
  should distinguish delta encoding from JSONL I/O and general compression.
- `ServiceSpec.socket_file` (`reccy/services/models.py:46-47`) produces `gui.sock`
  even though it is used as the generic control endpoint. GUI naming is misleading
  for headless consumers.
- `runtime.process` and `runtime.subprocess` overlap in command execution and
  reporting. `run_silent` captures output and logs failures, while `run` is largely
  a standard-library wrapper. Document which consumer requirement each owns.
- `revalidation_dump` duplicates `authored_dump(mode='python')`
  (`reccy/configuration/units.py:35-48`). Clarify a distinct semantic contract or
  consolidate the duplicated implementation if both names are needed.
- `ProtocolClient.shutdown()` (`reccy/protocol/ipc.py:193-194`) sends a request to
  shut down the peer; it does not close the local client. That distinction should
  be evident in the name and documentation.
- `run_main` converts KeyboardInterrupt into exit code zero (`reccy/cli.py:27-37`),
  so callers cannot distinguish an interrupted operation from success. Document
  this policy or use a distinct interruption result.

### R34 [P3, design] Some public concepts have no complete usage story

- `ipc.Reply` retains `id`, `ok`, and `result` (`reccy/protocol/ipc.py:41-46`), while
  RPC uses a direct result and no request ID. Nothing inside this repository uses
  `Reply`. Explain its separate protocol purpose or audit consumers before removal.
- `linux_xdg_autostart` renders a file, but `ServiceController` only manages
  systemd on Linux. Document whether callers must install/uninstall it themselves.
- `device_key` falls back to the display name (`reccy/device.py:27-31`); two
  identical devices can share that name. Clarify that this is not a uniqueness
  guarantee. `AudioMidiDeviceSpec` lists names but implements no matching policy,
  despite README's broad device-matching description.
- `status.updated_at` is saved and then read without a freshness policy
  (`reccy/reccy.py:22-25`; `reccy/services/controller.py:170-180`). A stopped or
  restarted service can expose stale health beside current manager status. State
  whether health is a last snapshot or current evidence.
- README lists module groups but provides no minimal Reccy subclass, lifecycle
  ownership example, RPC threading contract, or link to the Pytest plugin guide.
  These omissions make the public extension points harder to use correctly.

## Suggested order

Resolve transport cleanup, stale-socket deletion, persistence races, and lifecycle
cleanup first (R01-R05, R09-R10). Then address status/platform mismatches and
targeted configuration/fixture defects. Decide format and naming contracts
separately before changing consumer APIs. Each fix should add the focused
verification case described above; a passing existing suite would not close the
identified coverage gaps.

## Additional work beyond the prompt

None. This document records findings and follow-up directions only.
