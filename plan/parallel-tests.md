# Parallel unit tests

## Goal

Run reccy's complete unit-test suite concurrently without weakening its local
IPC, lifecycle, process, or atomic-file guarantees. The normal developer
command should reduce wall-clock time; `-n 0` remains available for
single-process debugging.

## Current safety assessment

The suite is suitable for process parallelism with explicit attention to its
local-resource tests:

- Tests use `tmp_path` for files, settings, service homes, resource claims, and
  generated service definitions.
- Tests that start live Unix-socket RPC servers create control and event paths
  inside `TemporaryDirectory(dir='/tmp')`. Each directory is unique, so workers
  do not bind the same socket pathname.
- Socket-pair, pipe, thread, and spawned-process tests keep their resources
  inside the test process or the test's temporary directory and close them in
  fixture cleanup or `finally` blocks.
- Fixed `/tmp` endpoint values in IPC tests are non-listening parser or fake
  connection inputs. They are not shared bound sockets.
- CLI and service tests monkeypatch process globals and environment variables
  per test. xdist workers use separate Python processes, and pytest restores
  each worker's monkeypatches between tests.

The parallel command remains a unit-test command. It must not be extended to
operate installed services, real endpoints, or external audio or MIDI devices.

## Implementation

1. Add unpinned `pytest-xdist` to the development dependency group and refresh
   `uv.lock` in a separate dependency commit. Preserve the existing one-week
   `exclude-newer` security cutoff.
2. Add pytest configuration so the normal command is:

   ```console
   uv run pytest -n auto --dist=worksteal
   ```

   `-n auto` uses the machine's physical CPU count. `worksteal` balances
   short model tests with slower RPC, socket, thread, and process tests without
   introducing file-level serialisation.
3. Document that developers may use `-n N` to cap workers and `-n 0` for
   single-process debugging. Keep output capture enabled; xdist cannot provide
   ordinary live `-s` output, so interactive investigations use `-n 0 -s`.
4. Run the complete suite serially and in parallel at least three times. Record
   wall-clock time and confirm identical collection and pass counts. Repeat the
   RPC, shutdown, limits, and resource-claim test modules as part of this check.
5. If parallel execution exposes a shared endpoint, path, process, or global
   setting, isolate it under `tmp_path` or a per-test temporary directory. Use
   `pytest.mark.xdist_group` only for a proven shared resource, never as a
   blanket workaround for a directory or module.

## Acceptance

- `uv run pytest -n auto --dist=worksteal` repeatedly passes with the same test
  count as `uv run pytest -n 0`.
- Repeated parallel runs of RPC, shutdown, limits, IPC, and resource-claim
  tests leave no socket files, child processes, or claim files outside their
  temporary directories.
- Parallel tests never contact an installed service, real endpoint, external
  device, or the network.
- The parallel command has a materially lower wall-clock time than a serial run
  measured on the same machine.
- Focused debugging remains possible with `-n 0`.

## References

pytest-xdist documents automatic worker selection, `-n 0`, and the
`worksteal` distribution mode at
<https://pytest-xdist.readthedocs.io/en/stable/distribution.html>.

## Additional work beyond the prompt

None.
