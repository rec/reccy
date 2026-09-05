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

The previous flat module paths remain as temporary compatibility imports. New
Reccy code uses the grouped module paths.

Application-specific recording, show control, audio, MIDI, lighting, web UI, and
IPC payloads stay in the consuming projects.
