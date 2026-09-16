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
- `reccy.device`: shared audio and MIDI device matching.

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
