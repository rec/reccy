"""Random integer sequences with a minimum gap between repeated values."""

from collections.abc import Iterator
from random import Random
from typing import Self

import pydantic


class Randomizer(pydantic.BaseModel):
    """Draw from range(n), optionally using every value once per cycle.

    The repeat interval applies across cycle boundaries as well as within them.
    Each iter() call keeps its own history and cycle. Supply a seeded Random
    on the model or iterator for reproducible draws.
    """

    n: int = pydantic.Field(gt=0, strict=True)
    dont_repeat_interval: int = pydantic.Field(
        default=None, strict=True, validate_default=True
    )
    use_all_before_repeating: bool = True
    random: Random | None = None

    def iter(self, random: Random | None = None) -> Iterator[int]:
        random = random or self.random or Random()
        recent: list[int] = []
        remaining: list[int] = []
        while True:
            if self.use_all_before_repeating:
                if not remaining:
                    remaining.extend(range(self.n))
                candidates = [x for x in remaining if x not in recent]
            else:
                candidates = [x for x in range(self.n) if x not in recent]
            value = random.choice(candidates)
            if self.use_all_before_repeating:
                remaining.remove(value)
            if self.dont_repeat_interval:
                recent.append(value)
                del recent[: -self.dont_repeat_interval]
            yield value

    @pydantic.field_validator('dont_repeat_interval', mode='before')
    @classmethod
    def resolve_interval(cls, value: object, info: pydantic.ValidationInfo) -> object:
        if value is None and 'n' in info.data:
            return info.data['n'] // 2
        return value

    @pydantic.model_validator(mode='after')
    def validate_interval(self) -> Self:
        if not 0 <= self.dont_repeat_interval < self.n:
            raise ValueError('dont_repeat_interval must satisfy 0 <= interval < n')
        return self

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True, frozen=True)
