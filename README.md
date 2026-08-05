# recy

Shared Python utilities for local command-line apps that need daemon control,
Tyro-based configuration, subprocess launching, and reusable Pydantic validators.

The initial consumers are expected to be `recs`, `showco`, `tuney`, and `lyte`.

## Included modules

- `recy.service`: per-user service install, uninstall, start, stop, restart, and
  status control for Linux `systemd --user`, macOS `launchd`, and Windows
  scheduled tasks.
- `recy.models`: generic service metadata, paths, daemon status, and result
  models.
- `recy.renderers`: service-definition renderers and metadata JSON rendering.
- `recy.paths`: platform-specific user config, state, service, log, and control
  endpoint paths.
- `recy.cli`: small first-token command routing and user-facing exception
  handling.
- `recy.config`: shared Tyro option and prefix parser helpers.
- `recy.subprocess`: frozen-aware app command construction and a no-shell
  subprocess wrapper.
- `recy.validators`: reusable value-level validators for Pydantic models.

Application-specific recording, show control, audio, MIDI, lighting, web UI, and
IPC payloads stay in the consuming projects.
