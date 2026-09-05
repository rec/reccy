from pathlib import Path

from reccy.services import runner


def test_runner_configures_log_before_running_daemon(monkeypatch) -> None:
    configured: list[tuple[Path, str]] = []
    modules: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runner.logging,
        'configure',
        lambda path, *, service_name: configured.append((path, service_name)),
    )
    monkeypatch.setattr(
        runner.runpy,
        'run_module',
        lambda module, *, run_name: modules.append((module, run_name)),
    )
    monkeypatch.setattr(
        runner.sys,
        'argv',
        ['runner', '/tmp/recs.log', 'recs', 'recs', '--silent'],
    )

    runner.main()

    assert configured == [(Path('/tmp/recs.log'), 'recs')]
    assert modules == [('recs', '__main__')]
    assert runner.sys.argv == ['recs', '--silent']
