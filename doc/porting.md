# Porting to grouped Reccy modules

Reccy's implementation modules are now grouped by responsibility. The old flat
module paths remain as temporary compatibility imports, so consumers can migrate
independently without coordinating releases. New code should use the grouped
paths.

This is an import-only change. Public classes and functions retain their existing
behavior, and objects imported through an old path are the same objects exposed
through the corresponding new path.

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
| `reccy.paths` | `reccy.services.paths` |
| `reccy.renderers` | `reccy.services.renderers` |
| `reccy.service_runner` | `reccy.services.runner` |
| `reccy.service_spec` | `reccy.services.spec` |

`reccy.cli`, `reccy.device`, `reccy.errors`, and `reccy.reccy` remain at their
existing paths.

The service compatibility module also exposes `current_platform` and
`service_paths`. Import those functions from `reccy.services.paths`, not
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

Existing service definitions that invoke `python -m reccy.service_runner`
continue to work through the compatibility entry point. Reinstall or regenerate
each application's service definition after porting so it records the canonical
runner path.

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

Do not remove the compatibility modules as part of porting one consumer. They
must remain until all Reccy consumers have migrated and a separate compatibility
removal is approved.
