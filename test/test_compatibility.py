from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ('old_module', 'new_module', 'symbol'),
    [
        ('reccy.config', 'reccy.configuration.tyro', 'tyro_option'),
        ('reccy.ipc', 'reccy.protocol.ipc', 'ProtocolClient'),
        ('reccy.jsonl', 'reccy.protocol.jsonl', 'Jsonl'),
        ('reccy.logging', 'reccy.runtime.logging', 'configure'),
        ('reccy.models', 'reccy.services.models', 'ServiceSpec'),
        ('reccy.paths', 'reccy.services.paths', 'service_paths'),
        ('reccy.process', 'reccy.runtime.process', 'ManagedProcess'),
        ('reccy.renderers', 'reccy.services.renderers', 'service_metadata'),
        ('reccy.rpc', 'reccy.protocol.rpc', 'Client'),
        ('reccy.service', 'reccy.services.controller', 'ServiceController'),
        ('reccy.service_runner', 'reccy.services.runner', 'main'),
        ('reccy.service_spec', 'reccy.services.spec', 'load'),
        ('reccy.settings', 'reccy.configuration.settings', 'load'),
        ('reccy.subprocess', 'reccy.runtime.subprocess', 'app_command'),
        ('reccy.units', 'reccy.configuration.units', 'Seconds'),
        ('reccy.validators', 'reccy.configuration.validators', 'identifier'),
    ],
)
def test_old_module_exports_canonical_symbol(
    old_module: str, new_module: str, symbol: str
) -> None:
    old = import_module(old_module)
    new = import_module(new_module)

    assert getattr(old, symbol) is getattr(new, symbol)
