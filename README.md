# reccy

Shared Python utilities for local command-line apps that need daemon control,
Tyro-based configuration, subprocess launching, and reusable Pydantic validators.

The initial consumers are expected to be `recs`, `showco`, `tuney`, and `lyte`.

## Included modules

- `reccy.protocol`: IPC transports, RPC clients and servers, and JSONL stream
  compression.
- `reccy.services`: per-user service definitions, rendering, paths, lifecycle
  control, and runner support for Linux `systemd --user`, macOS `launchd`, and
  Windows scheduled tasks.
- `reccy.configuration`: Tyro helpers, settings persistence, unit-aware Pydantic
  numeric types, and reusable value validators.
- `reccy.runtime`: logging and child-process utilities.
- `reccy.reccy`: shared application lifecycle, status, settings, RPC, and
  service integration.
- `reccy.cli`: first-token command routing and user-facing exception handling.
- `reccy.device`: audio/MIDI candidate-name specifications and device-key helpers.

The previous flat module paths have been removed. Reccy code and consumers use
the grouped module paths.

Application-specific recording, show control, audio, MIDI, lighting, web UI, and
IPC payloads stay in the consuming projects.

## IPC endpoints and lifecycle

IPC accepts filesystem socket paths as either `Path` or `str`. Local Windows
named-pipe addresses, such as `\\.\pipe\app`, select the pipe transport regardless
of the Python argument type. Serialized service metadata endpoints can therefore
be passed directly to an RPC client.

`ProtocolClient` and `EventClient` are single-use after connecting; create a new
client to reconnect. Both expose `close()` to release their local connection.
`ProtocolClient.shutdown()` requests shutdown of the peer instead. Application
messages are accepted only after hello, and event subscription startup has a
one-second deadline. Set RPC request deadlines using
`rpc.Client(endpoint, timeout=...).call(...)`.

RPC server shutdown disconnects accepted clients, including incomplete handshakes.
Application handlers already executing may finish; shutdown does not forcibly
interrupt them.

RPC handlers return raw strings or dictionaries for success, and `ipc.Error`
for failure. The top-level dictionary discriminator `type="error"` is reserved
for protocol errors and must not be used in successful results. Errors require
a string `message`; clients raise `ConnectionError` with that message. Application
data describing an error can use a different discriminator or a nested object.
This contract does not require a response envelope or a wire-version change.

`Reccy.start()` rejects an already-started application. `close()` is idempotent.
RPC cleanup and `on_closed()` run even if startup, shutdown hooks, or status
publication fail. Release application-owned resources in `on_closed()` and allow
for partial startup there. Exceptions still propagate to the caller.

Set `status_model` to a `ReccyStatus` subclass to enable status persistence. The
default snapshot constructs that model, and the service controller reads the same
model. Override `status_snapshot()` when additional required fields need values.
Status retains the latest 1,000 errors. Every error is also logged; older log
history is subject to the configured log retention policy.

Service lifecycle results confirm manager command completion, not the running
state (`running=None`). Call `status()` to observe the manager's current state.
Installation requests startup on every platform. Uninstall errors propagate and
retain local metadata; an already-unloaded service may require manager-specific
attention before uninstall can complete. Linux reloads its manager after removing
the unit file and before deleting metadata.

`logging.configure(path=..., service_name=...)` replaces root handlers and redirects
stdout/stderr to the rotating file. Repeating the same path reuses the stream.
Without a path, existing handlers are preserved and only the log level changes.

## Service installation contract

Installation requires a normal Python interpreter; frozen applications are
rejected before any files are written. Definitions use the installing interpreter
and run `reccy.services.runner`, which configures logging and runs the application
module with the recorded arguments. Reinstall after changing interpreters.

Metadata version 1 describes the installed module, arguments, platform and
endpoints. Clients may use its endpoints for discovery, but the runner does not
load this file or inject endpoints into the application. Applications own their
endpoint configuration and must agree with the installation metadata. Unsupported
metadata versions fail validation.

An explicit `home` controls generated file paths and the working directory. On
Windows it also overrides APPDATA/LOCALAPPDATA; those environment variables are
used only when home is omitted. These paths do not change the service-manager
account: installation still targets the current user.

## Unit-aware configuration dumps

`runtime_dump()` emits normalized numbers; `authored_dump()` retains authored
unit strings. Both emit canonical field names, even when a model has aliases or
enables alias serialization. `revalidation_dump()` is the Python-mode authored
dump intended for rebuilding a model without losing provenance. Models using
validation aliases must also accept field names to revalidate these dumps.

These helpers support ordinary models, nested models, lists and dictionaries.
They reject `RootModel` and custom field/model or annotated serializers with
`TypeError`, including in nested values. Use Pydantic's own dump methods when
custom serialization is needed, without unit-provenance restoration.

`collect_unit_provenance()` returns JSON Pointer paths: `/nested/delay` and
`/history/0`, with `~` escaped as `~0` and `/` as `~1`. Dictionary keys must be
strings. The empty path identifies a quantity passed directly to the collector.

`named_choice_spec()` (formerly `prefix_spec`) parses exact choice names, not
prefixes. Formatting selects the first matching name in mapping order, so aliases
for the same value are deterministic and formatted values parse back successfully.

## Dictionary delta streams

`reccy.protocol.jsonl.Compress` and `Decompress` operate on dictionaries, not JSON
text or line framing. Non-key fields that are absent or `None` are equivalent in
full records: an initially null field is omitted; clearing a previously non-null
field emits `None`, which decompression retains. Exact dictionary reconstruction
is not promised. In an encoded delta, an absent field means unchanged.

Each codec keeps state per string key across calls. Consume batches in order and
create fresh codec instances for every independent stream. A fresh decoder cannot
reconstruct values omitted by a compressor continuing an earlier stream.

## Child-process output

`capture_stderr()` drains in chunks of at most 4,096 bytes, splitting oversized
lines. Its `OutputTail` retains at most 80 chunks of 4,096 characters. Callbacks
receive those chunks, not necessarily complete lines; invalid or split UTF-8 is
decoded with replacement. If a callback raises, capture stops calling it but
continues draining before reporting the exception through `threading.excepthook`.
After child exit, `tail.wait(timeout)` waits for capture completion and returns
whether it finished. A blocking callback can still delay capture.

Use `runtime.process` for managed child lifetimes, bounded output tails and
`run_silent()` (capture output, log it on failure, then re-raise).
`runtime.subprocess.run()` is the configurable standard-library wrapper;
`app_command()` chooses source versus frozen command arguments for ordinary
application launches, not service installation.

## Application lifecycle and extension points

`Reccy` is the application/daemon lifecycle base, not an event loop. A minimal
application can publish status without enabling RPC or service installation:

```python
from reccy.reccy import Reccy, ReccyStatus


class Application(Reccy):
    name = 'example'
    status_model = ReccyStatus


def main() -> None:
    application = Application()
    try:
        application.start()
        print(application.status_snapshot().model_dump())
    finally:
        application.close()
```

The caller owns the run loop and calls start/close serially. `on_started()` may
allocate application resources; `on_stopping()` runs before shutdown publication;
`on_closed()` releases resources even after partial startup. Cleanup must tolerate
missing resources. Override `status_snapshot()`, `rpc_command()` and mutable
attribute hooks only as needed. Set `rpc_enabled=True` to expose RPC, and provide
`service_spec` separately for service-manager integration.

RPC commands execute concurrently in worker threads. Applications must protect
their own shared state and coordinate shutdown with running handlers; closing the
server disconnects clients but does not stop executing handlers. Event callbacks
run on the event-reader thread, so they must not block that reader.

Controller `health` is the last saved status snapshot, not proof of current
health. Inspect its `updated_at` using an application-appropriate freshness limit;
the manager's `running` value is a separate observation.

## API naming and ownership notes

- `cli_help` performs a regression assertion and returns nothing. Its short name
  is retained for consumer tests. See the [CLI help guide](doc/testing-cli-help.md).
- `Jsonl`, `Compress` and `Decompress` retain their existing names for consumers;
  they encode dictionary deltas as described above, not JSONL text or compression
  of arbitrary data.
- `ServiceSpec.socket_file` is the generic control socket's relative path. Its
  existing `gui.sock` basename is retained to avoid changing endpoint discovery;
  it does not imply a GUI-only service.
- `ProtocolClient.shutdown()` sends a peer-shutdown request; `close()` releases
  the local connection. Neither name is an alias for the other.
- `run_main()` reports interruption on stderr and returns 130, distinct from
  successful completion.
- `linux_xdg_autostart()` only renders a desktop entry. Its caller owns writing
  and removing that file. `ServiceController` manages systemd on Linux, not XDG
  autostart entries.
- `AudioMidiDeviceSpec` stores candidate names; consumers own matching and
  ambiguity handling. `device_key()` prefers a supplied persistent ID but falls
  back to a display name, which is not guaranteed unique.
- Control RPC has no request IDs or reply envelope; use raw results or
  `ipc.Error`. The unused `ipc.Reply` model has been removed.
