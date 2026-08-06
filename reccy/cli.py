import sys
from collections.abc import Callable, Mapping

from pydantic import ValidationError

from .errors import ReccyError


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
        return 0
    except ValidationError as e:
        print('ERROR:', e, file=sys.stderr)
    except ReccyError as e:
        print('ERROR:', *e.args, file=sys.stderr)
    return 1


def _usage(prog: str, commands: Mapping[str, object]) -> str:
    return f'Usage: {prog} {{{",".join(commands)}}} ...'
