from __future__ import annotations

import tomllib
from pathlib import Path

from . import models


def load(path: Path) -> models.ServiceSpec:
    with path.open('rb') as file:
        return models.ServiceSpec.model_validate(tomllib.load(file))
