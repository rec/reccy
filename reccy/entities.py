"""Named entities shared by recording applications."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from .configuration.validators import identifier


class Entity(BaseModel):
    name: Annotated[str, AfterValidator(identifier)]
    other_names: list[str] = Field(default_factory=list)
    copyright_name: str | None = None
    public_keys: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    templates: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class Musician(Entity):
    pass


class Project(Entity):
    pass
