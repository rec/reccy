# Regression testing CLI help

## Goal

Provide a small Reccy test utility that records the public help for one CLI in
one text regression fixture. It will replace Tuney's top-level help regression
test and be usable by the other command-line applications.

## Interface

Import `cli_help` from `reccy.testing`. It receives the program name, an
application-supplied adapter, Pytest's `capsys` and `monkeypatch` fixtures, and
an optional iterable of immediate subcommand names:

```python
from reccy.testing import cli_help


def test_help(file_regression, capsys, monkeypatch) -> None:
    file_regression.check(
        cli_help('my-app', main, capsys, monkeypatch, subcommands=['config', 'run'])
    )
```

The adapter accepts the argument list without the program name and returns zero
for successful help. `SystemExit(0)` is also accepted, so a Tyro entry point can
be passed directly when it accepts argv.

The adapter keeps the facility independent of a CLI framework and of how an
application accepts arguments. For example, a CLI whose `main` accepts argv can
pass that function directly. Tuney's `sys.argv` entry point needs this adapter:

```python
def invoke(arguments: list[str]) -> int:
    monkeypatch.setattr(sys, 'argv', ['tuney', *arguments])
    return main()
```

Command names remain explicit because the applications currently expose
subcommands through different mechanisms, and parsing formatted help to discover
them would make the test depend on each framework's presentation.

## Recorded output

The helper invokes the adapter with `['--help']` first. It then invokes it with
`[subcommand, '--help']` for every supplied immediate subcommand, sorted
alphabetically. Each successful response is appended to the same regression text
with the invoked command as a visible heading, followed by its captured help
output:

```text
$ my-app --help
...top-level help...

$ my-app config --help
...config help...
```

This makes additions, removals, and changes to any public command help reviewable
in one fixture.

The helper treats a normal zero return and `SystemExit(0)` as successful help
paths, and fails the test for any other outcome. It sets `COLUMNS=120` and
`NO_COLOR=1`, then removes trailing line padding and normalizes the two bullet
renderings currently handled by Tuney. It captures standard output only: help
written to standard error or an unsuccessful exit is a CLI failure, not fixture
content.

## Consumer test shape

Each application keeps a thin test that supplies its adapter and subcommands,
then passes the returned text to its existing `file_regression` fixture. A
single-command CLI omits subcommands and therefore records only its top-level
help. Applications list only their direct public subcommands. Nested command
groups use a separate invocation of the utility if their help needs its own
fixture.

## Rollout

1. Move Tuney's normalization and top-level regression test to the helper,
   retaining Tuney's separate option-policy tests.
2. Adopt it in each other CLI application where stable help output is part of the
   public interface, adding `pytest-regressions` only to consumers that do not
   already provide `file_regression`.
