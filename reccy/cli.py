import subprocess
import sys
from collections.abc import Callable, Mapping
from typing import Annotated

import tyro
from pydantic import BaseModel, ValidationError

from .documentation import Documentation
from .errors import ReccyError
from .release import ReleaseCommand, release


class DocsCommand(BaseModel, frozen=True):
    """Build documentation and push it to GitHub Pages."""

    init: bool = False
    """Initialize GitHub Pages if the repository does not have a Pages site."""


def main(argv: list[str] | None = None) -> int:
    """Run the installed reccy docs or release command for the current project."""
    command = tyro.cli(
        Annotated[DocsCommand, tyro.conf.subcommand(name='docs')]
        | Annotated[ReleaseCommand, tyro.conf.subcommand(name='release')],
        args=argv,
        prog='reccy',
    )
    if isinstance(command, DocsCommand):
        return run_main(lambda: _publish_docs(command))
    return run_main(lambda: release(command))


def route_command(
    commands: Mapping[str, Callable[[list[str]], int]],
    argv: list[str] | None = None,
    *,
    prog: str,
) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments or arguments[0] in {'-h', '--help'}:
        print(_usage(prog, commands))
        return 0
    command = arguments[0]
    if command not in commands:
        print(f'unknown command: {command}', file=sys.stderr)
        print(_usage(prog, commands), file=sys.stderr)
        return 2
    return commands[command](arguments[1:])


def run_main(action: Callable[[], int]) -> int:
    try:
        return action()
    except KeyboardInterrupt:
        print('Interrupted', file=sys.stderr)
        return 130
    except ValidationError as e:
        print('ERROR:', e, file=sys.stderr)
    except ReccyError as e:
        print('ERROR:', *e.args, file=sys.stderr)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print('ERROR:', e, file=sys.stderr)
    return 1


def _usage(prog: str, commands: Mapping[str, object]) -> str:
    return f'Usage: {prog} {{{",".join(commands)}}} ...'


def _publish_docs(command: DocsCommand) -> int:
    Documentation().publish(init=command.init)
    return 0
