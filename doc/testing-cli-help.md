# Regression testing CLI help

## Goal

Provide a small Reccy test utility that records the public help for one CLI in
one text regression fixture. It will replace Tuney's top-level help regression
test and be usable by the other command-line applications.

## Interface

Enable Reccy's Pytest plugin in the consumer's top-level `conftest.py`:

```python
pytest_plugins = ['reccy.pytest_plugin']
```

The plugin provides a `cli_help` fixture. It owns Pytest's capture, environment,
and `file_regression` fixtures. The test supplies only the program name, an
application-specific adapter, and an optional iterable of immediate subcommands:

```python
def test_help(cli_help) -> None:
    cli_help('my-app', main, subcommands=['config', 'run'])
```

The fixture sets `sys.argv` for each command before it calls the entry point. The
entry point returns zero or `None` for successful help; `SystemExit(0)` and
`SystemExit()` are also accepted.
This lets a typical CLI, including Tuney's, pass `main` directly. An entry point
that requires an argv list can read the supplied `sys.argv` in a small wrapper:

```python
cli_help('my-app', lambda: main(sys.argv[1:]))
```

The adapter keeps the facility independent of a CLI framework and of how an
application accepts arguments. The fixture supplies the argument list through
`sys.argv`, so consumer tests never need Pytest's capture or monkeypatch fixtures.

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

The helper accepts zero or `None`, whether returned or carried by `SystemExit`,
and fails the test for any other outcome. It sets `COLUMNS=120` and
`NO_COLOR=1`, then removes trailing line padding and normalizes the two bullet
renderings currently handled by Tuney. It captures standard output only: help
written to standard error or an unsuccessful exit is a CLI failure, not fixture
content.

Capture is drained before and after every invocation, including failures. Earlier
test output is excluded. Changes to argv and environment are restored by Pytest
at test teardown.

## Consumer test shape

Each application keeps a thin test that supplies its adapter and subcommands. A
single-command CLI omits subcommands and therefore records only its top-level
help. Applications list only their direct public subcommands. Nested command
groups use separate test functions so each gets its own baseline. Call `cli_help`
once per test. The consumer must have `pytest-regressions` installed so its
`file_regression` fixture is available to the plugin.

For a nested group, the display label is not an argument prefix. Insert the group
tokens in the adapter explicitly:

```python
def test_config_help(cli_help) -> None:
    def invoke() -> int | None:
        return main(['config', *sys.argv[1:]])

    cli_help('my-app config', invoke, subcommands=['get', 'set'])
```

Here `main` accepts an argv list. For an entry point that reads `sys.argv`, replace
its contents in the adapter with `['my-app', 'config', *sys.argv[1:]]`, then call
`main()`. The fixture resets argv before each invocation.

`test/test_cli_help_regression.py` exercises real baseline creation, matching and
mismatch detection when pytest-regressions is installed. It skips otherwise, so
Reccy does not require that optional consumer test dependency.

## Rollout

1. Move Tuney's normalization and top-level regression test to the helper,
   retaining Tuney's separate option-policy tests.
2. Adopt it in each other CLI application where stable help output is part of the
   public interface, adding `pytest-regressions` only to consumers that do not
   already provide `file_regression`.
