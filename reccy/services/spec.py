from __future__ import annotations

from pathlib import Path

import tomli

from . import models


def load(path: Path) -> models.ServiceSpec:
    with path.open('rb') as file:
        return models.ServiceSpec.model_validate(tomli.load(file))
