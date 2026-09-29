# Porting to current reccy

The service and RPC lifecycle interfaces changed in commit `e7a2171`. There
are no compatibility adapters. Update consumers and their tests before using
this reccy version.

## Service commands return no status

`ServiceController.install()`, `uninstall()`, `start()`, `stop()`, and
`restart()` now return `None` on success and raise on failure. The corresponding
`Reccy.install_service()`, `uninstall_service()`, `start_service()`,
`stop_service()`, and `restart_service()` methods do the same. Only `status()`
and `service_status()` return `StatusResult`.

A successful manager command means the command completed, not that the daemon
is already running or healthy. Remove reads of `.installed` or `.running` from
command results. For an acknowledgement, report the command's success after it
returns. For observed state, call `status()` separately and allow for service
startup to be asynchronous. Do not turn an immediate `running=False` into a
failed command unless that is the application's deliberate readiness policy.

```python
controller.restart()
print('restart requested')
observed = controller.status()
print(observed.running)
```

In particular, recs' daemon CLI and baccy's service CLI currently serialize
command results, while showCo, lyte, and streamO inspect or print them. Their
output and tests must be updated. showCo's `restart_service()` currently tests
`.running` on a restart result that never measured running state; report
command success there or implement an explicit readiness check.

## RPC handlers receive cancellation

`rpc.Server` now calls `handle(request, cancelled)`, where `cancelled` is a
`threading.Event`. It is set when the request deadline expires or server
shutdown begins. Handlers must check it before side effects and during any
wait or long operation, then return promptly. If a handler delegates work to
another thread or queue, propagate cancellation to that work and wait for it
to finish before returning. A Python thread cannot be forcibly interrupted.

`rpc.Server(..., request_timeout=seconds)` defaults to 30 seconds and rejects
nonpositive values. `Reccy.rpc_request_timeout` sets this deadline for
Reccy-owned servers. Override `Reccy.rpc_response(request, cancelled)` or
`rpc_command(request, cancelled)` with the new second parameter. recs and lyte
override `rpc_response()` and need corresponding signature and cancellation
updates. Match the deadline to each application's actual work and the RPC
client's timeout; a client timing out does not undo a handler's side effects.

`rpc.Server.close(timeout=seconds)` cancels active requests, closes transports,
and waits up to five seconds by default for accept, request, subscription, and
event-writer threads. It raises `TimeoutError` if any remain. On that failure,
`Reccy.close()` retains the server and does **not** call `on_closed()`, so
resources owned by `on_closed()` are not released under an active handler. Once
the handler finishes, call `close()` again to complete teardown. If a handler
ignores cancellation, its request slot remains occupied and shutdown cannot
guarantee safe resource release.

## Event subscribers are isolated

`rpc.Server.publish()` queues events instead of writing to subscribers on the
publisher's thread. Each subscriber has a four-event pending queue. Events
remain ordered for a connected subscriber; a subscriber whose queue fills is
disconnected rather than given a partial stream. Consumer event clients must
handle disconnection and reconnect if continued updates are required.

## Generic IPC lifecycle

`ProtocolClient.request_shutdown()` replaces `shutdown()`. It requires a live,
completed handshake, raises `RuntimeError` before that point, and raises
`BrokenPipeError` if the write fails. A successful return confirms only local
write success, not that the peer accepted or completed shutdown. `close()`
still disconnects only the local client.

`ProtocolListener.wait_closed(timeout)` now waits for its reader thread and
returns `False` on timeout. Call it after `close()` when resource teardown must
wait for the listener to finish. It raises if `start()` was never called or if
the listener thread tries to wait for itself.

## Previous grouped-module migration

Reccy's implementation modules are grouped by responsibility. The old flat
module paths have been removed; consumers must use the grouped paths.

That earlier migration was an import-only change. The lifecycle changes above
are separate behavior and signature changes.

## Module mapping

| Old module | New module |
| --- | --- |
| `reccy.config` | `reccy.configuration.tyro` |
| `reccy.settings` | `reccy.configuration.settings` |
| `reccy.units` | `reccy.configuration.units` |
| `reccy.validators` | `reccy.configuration.validators` |
| `reccy.ipc` | `reccy.protocol.ipc` |
| `reccy.rpc` | `reccy.protocol.rpc` |
| `reccy.jsonl` | `reccy.protocol.jsonl` |
| `reccy.logging` | `reccy.runtime.logging` |
| `reccy.process` | `reccy.runtime.process` |
| `reccy.subprocess` | `reccy.runtime.subprocess` |
| `reccy.service` | `reccy.services.controller` |
| `reccy.models` | `reccy.services.models` |
| old `reccy.paths` service paths | `reccy.services.paths` |
| `reccy.renderers` | `reccy.services.renderers` |
| `reccy.service_runner` | `reccy.services.runner` |
| `reccy.service_spec` | `reccy.services.spec` |

`reccy.cli`, `reccy.device`, `reccy.errors`, and `reccy.reccy` remain at their
existing paths.

The current `reccy.paths` is a separate module for filename and readable URL
path transformations. Its output is lossy; callers must resolve collisions.

Import `current_platform` and `service_paths` from `reccy.services.paths`, not
`reccy.services.controller`.

## Updating imports

Replace imports of flat modules with imports from their owning package. Preserve
the existing import style and local names where practical so the migration does
not change application code beyond the import statements.

```python
# Before
from reccy import ipc, rpc
from reccy.models import ServiceSpec
from reccy.units import Seconds

# After
from reccy.configuration.units import Seconds
from reccy.protocol import ipc, rpc
from reccy.services.models import ServiceSpec
```

Modules whose names changed need corresponding import updates:

```python
# Before
from reccy.service import ServiceController

# After
from reccy.services.controller import ServiceController
```

Avoid adding imports to the package `__init__.py` files. Import modules or symbols
directly from the files in which they are defined.

## Service runner

The canonical module invocation for the service runner is now:

```text
python -m reccy.services.runner
```

Existing service definitions that invoke `python -m reccy.service_runner` no
longer work. Reinstall or regenerate each application's service definition after
porting so it records the canonical runner path.

## Suggested procedure

1. Find references to the old modules in the consumer's source, tests, scripts,
   and service templates.
2. Replace each import using the mapping above.
3. Update patches or monkeypatches to target the canonical module. For example,
   patch `reccy.services.controller`, not `reccy.service`.
4. Run the consumer's complete test and static-checking workflow.
5. Reinstall its user service, if it has one, and verify that the generated
   definition invokes `reccy.services.runner`.

A useful initial search is:

```shell
rg 'reccy\.(config|settings|units|validators|ipc|rpc|jsonl|logging|process|subprocess|service|models|paths|renderers|service_runner|service_spec)'
```
