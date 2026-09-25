import pytest
from pydantic import ValidationError

from reccy.entities import Musician, Project


def test_entities_share_metadata() -> None:
    musician = Musician(name='mike', other_names=['Michael'])
    project = Project(
        name='x18-show',
        links=['https://example.com'],
        templates={'index': '<h1>X18</h1>'},
    )

    assert musician.other_names == ['Michael']
    assert project.links == ['https://example.com']
    assert project.templates == {'index': '<h1>X18</h1>'}


def test_entities_require_identifiers() -> None:
    with pytest.raises(ValidationError, match='lowercase'):
        Musician(name='Mike')
