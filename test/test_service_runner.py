from pathlib import Path

from reccy import service_runner


def test_runner_configures_log_before_running_daemon(monkeypatch) -> None:
    configured: list[Path] = []
    modules: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service_runner.logging, 'configure', lambda path: configured.append(path)
    )
    monkeypatch.setattr(
        service_runner.runpy,
        'run_module',
        lambda module, *, run_name: modules.append((module, run_name)),
    )
    monkeypatch.setattr(
        service_runner.sys,
        'argv',
        ['runner', '/tmp/recs.log', 'recs', '--silent'],
    )

    service_runner.main()

    assert configured == [Path('/tmp/recs.log')]
    assert modules == [('recs', '__main__')]
    assert service_runner.sys.argv == ['recs', '--silent']
