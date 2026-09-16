# Regression testing CLI help

## Goal

Provide a small Reccy test utility that records the public help for one CLI in
one text regression fixture. It will replace Tuney's top-level help regression
test and be usable by the other command-line applications.

## Interface

The utility will receive:

- the program name, used when rendering recorded commands;
- an application-supplied adapter that invokes its CLI with an argument list
  and returns or exposes its successful exit status; and
- an optional iterable of immediate subcommand names.

The adapter keeps the facility independent of a CLI framework and of how an
application accepts arguments. For example, a CLI whose `main` accepts argv can
pass that function directly, while Tuney's test can adapt its `sys.argv` entry
point. Command names remain explicit because the applications currently expose
subcommands through different mechanisms, and parsing formatted help to discover
them would make the test depend on each framework's presentation.

## Recorded output

The helper will invoke the adapter with `['--help']` first. It will then invoke
it with `[subcommand, '--help']` for every supplied immediate subcommand, sorted
alphabetically. Each successful response will be appended to the same regression
text with the invoked command as a visible heading, followed by its captured help
output. This makes additions, removals, and changes to any public command help
reviewable in one fixture.

The helper will treat a normal zero return and `SystemExit(0)` as successful help
paths, and fail the test for any other outcome. It will set a fixed terminal
width and disable colour, then remove trailing line padding and normalize the
two bullet renderings currently handled by Tuney. It will capture standard output
only: help written to standard error or an unsuccessful exit is a CLI failure,
not fixture content.

## Consumer test shape

Each application will keep a thin test that supplies its adapter and subcommands,
then passes the returned text to its existing `file_regression` fixture. A
single-command CLI will omit subcommands and therefore record only its top-level
help. Applications will list only their direct public subcommands; nested command
groups can use a separate invocation of the utility if their help needs its own
fixture.

## Rollout

1. Add the framework-neutral helper under Reccy's test-facing utilities, with a
   focused Reccy test covering command order, command headings, and successful
   exit handling.
2. Move Tuney's normalization and top-level regression test to the helper,
   retaining Tuney's separate option-policy tests.
3. Adopt it in each other CLI application where stable help output is part of the
   public interface, adding `pytest-regressions` only to consumers that do not
   already provide `file_regression`.
