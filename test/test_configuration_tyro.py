from typing import Annotated

import pytest
import tyro
from pydantic import BaseModel

from reccy.configuration import units
from reccy.configuration.tyro import unit_spec


class UnitCommand(BaseModel, frozen=True):
    interval: Annotated[units.Seconds, unit_spec(units.Seconds, 'SECONDS')] = 1
    duration: Annotated[units.Seconds, unit_spec(units.Seconds, 'SECONDS')] | None = (
        None
    )


@pytest.mark.parametrize('option', ['--interval', '--duration'])
def test_invalid_units_report_cli_error(
    option: str, capsys: pytest.CaptureFixture
) -> None:
    with pytest.raises(SystemExit) as error:
        tyro.cli(UnitCommand, args=[option, '5m'])

    assert error.value.code != 0
    output = capsys.readouterr().err
    assert option in output
    assert 'Expected second' in output
    assert 'Traceback' not in output
