"""Real subprocess checks with fake workers; no QQ, DB or production processes."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sys
import subprocess
import threading
import time

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from qq_runtime import FileLock, RuntimePaths, atomic_json, locked, managed_run, read_json
from qq_service import Supervisor, local_status, start, stop
import qq_service


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Fake QQ worker did not reach expected state')


def fake_command(tmp_path, failures=0, exit_code=17, lease_seconds=20):
    script = tmp_path / 'fake_worker.py'
    script.write_text(f'''
import asyncio, os, sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
from qq_runtime import FileLock, RuntimePaths, managed_run
paths = RuntimePaths(Path({str(tmp_path / 'qq')!r}))
counter = paths.home / 'attempts.txt'
with FileLock(paths.worker_lock):
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    if count <= {failures}:
        os._exit({exit_code})
    async def serve():
        while True:
            await asyncio.sleep(.02)
    asyncio.run(managed_run(serve(), paths, sys.argv[-1], lease_seconds={lease_seconds}, interval=.02))
''', encoding='utf-8')
    return [sys.executable, str(script)]


@contextmanager
def running(tmp_path, failures=0, exit_code=17, **kwargs):
    paths = RuntimePaths(tmp_path / 'qq')
    supervisor = Supervisor(paths, fake_command(tmp_path, failures, exit_code), poll=.02, base_delay=.02, **kwargs)
    results = []
    def run():
        try:
            results.append(supervisor.run())
        except Exception as exc:
            results.append(exc)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield paths, supervisor, thread, results
    finally:
        atomic_json(paths.stop, {'run_id': supervisor.run_id})
        thread.join(8)
        assert not thread.is_alive(), 'Test supervisor did not stop its own fake worker'
        assert not results or not isinstance(results[0], Exception), results


def attempts(paths):
    file = paths.home / 'attempts.txt'
    try:
        return int(file.read_text())
    except (OSError, ValueError):
        return 0


def test_worker_crash_recovers_and_explicit_stop_stays_stopped(tmp_path):
    with running(tmp_path, failures=2) as (paths, supervisor, thread, results):
        # Three real interpreter launches can exceed 12s on a busy Windows host.
        # Keep recovery/attempt assertions unchanged; bound only startup waiting.
        wait_for(lambda: attempts(paths) == 3 and locked(paths.worker_lock), timeout=60)
        assert local_status(paths)['manager_running']
        assert read_json(paths.state)['restarts'] == 2
        assert start(paths) == 0  # Second start cannot launch another manager/worker.
        assert Supervisor(paths, ['unused']).run() == 2
        assert stop(paths, timeout=6) == 0
        thread.join(3)
        assert results == [0]
        assert read_json(paths.state)['state'] == 'stopped'
        assert not locked(paths.worker_lock)
        assert attempts(paths) == 3


@pytest.mark.parametrize('exit_code,expected_attempts,reason', [(17, 3, 'repeated_failures'), (2, 1, 'configuration_or_duplicate_worker')])
def test_failure_limit_and_permanent_failure_pause_recovery(tmp_path, exit_code, expected_attempts, reason):
    with running(tmp_path, failures=20, exit_code=exit_code, max_failures=3) as (paths, _, thread, results):
        wait_for(lambda: not thread.is_alive())
        assert results == [1]
        state = read_json(paths.state)
        assert state['state'] == 'blocked' and state['reason'] == reason
        assert state['last_exit']['code'] == exit_code
        assert attempts(paths) == expected_attempts


def test_stop_during_backoff_does_not_launch_another_worker(tmp_path):
    with running(tmp_path, failures=20) as (paths, supervisor, thread, _):
        supervisor.base_delay = 3
        wait_for(lambda: read_json(paths.state).get('state') == 'restarting')
        assert stop(paths, timeout=5) == 0
        thread.join(2)
        assert attempts(paths) == 1


def test_transient_state_write_denial_does_not_kill_supervisor(tmp_path, monkeypatch):
    original = qq_service.atomic_json
    failures = []

    def deny_running_state(path, data):
        if path.name == 'service.json' and data.get('state') == 'running' and len(failures) < 3:
            failures.append(True)
            raise PermissionError('simulated sharing violation')
        return original(path, data)

    monkeypatch.setattr(qq_service, 'atomic_json', deny_running_state)
    with running(tmp_path) as (paths, _, thread, results):
        wait_for(lambda: len(failures) == 3 and read_json(paths.state).get('state') == 'running'
                 and locked(paths.worker_lock))
        assert thread.is_alive() and results == []
        assert attempts(paths) == 1
        assert 'state_write_deferred' in (paths.home / 'service.log').read_text(encoding='utf-8')
        assert stop(paths, timeout=6) == 0
        thread.join(3)
        assert results == [0]
        assert not locked(paths.worker_lock)


def test_launch_waits_for_current_state_and_blocks_when_unwritable(tmp_path, monkeypatch):
    paths = RuntimePaths(tmp_path / 'qq')
    original = qq_service.atomic_json
    atomic_json(paths.state, {'run_id': 'previous-owner', 'heartbeat': 0})
    supervisor = Supervisor(paths, ['must-not-launch'], poll=.01)
    failures = []

    def fail_once(path, data):
        if not failures:
            failures.append(True)
            raise PermissionError('simulated sharing violation')
        return original(path, data)

    monkeypatch.setattr(qq_service, 'atomic_json', fail_once)
    assert supervisor.prepare_launch(timeout=.2)
    assert read_json(paths.state)['run_id'] == supervisor.run_id

    def always_fail(*_):
        raise PermissionError('simulated persistent failure')

    monkeypatch.setattr(qq_service, 'atomic_json', always_fail)
    assert not supervisor.prepare_launch(timeout=.02)
    atomic_json(paths.stop, {'run_id': supervisor.run_id})
    assert not supervisor.prepare_launch(timeout=.2)


def test_external_worker_is_not_killed_or_duplicated(tmp_path):
    paths = RuntimePaths(tmp_path / 'qq')
    with FileLock(paths.worker_lock):
        assert start(paths) == 2
        assert Supervisor(paths, ['must-not-launch']).run() == 2
        assert stop(paths) == 2
        assert locked(paths.worker_lock)


def test_old_stop_request_cannot_stop_a_new_instance(tmp_path):
    paths = RuntimePaths(tmp_path / 'qq')
    atomic_json(paths.stop, {'run_id': 'old-instance'})
    with running(tmp_path) as (paths, supervisor, _, _):
        wait_for(lambda: locked(paths.worker_lock))
        assert read_json(paths.state)['run_id'] == supervisor.run_id
        assert local_status(paths)['worker_running']


def test_lease_loss_cancels_worker_and_runs_cleanup(tmp_path):
    paths = RuntimePaths(tmp_path / 'qq')
    atomic_json(paths.state, {'run_id': 'current', 'heartbeat': time.time() - 30})
    cleaned = []
    async def worker():
        try:
            await asyncio.sleep(100)
        finally:
            cleaned.append(True)
    asyncio.run(managed_run(worker(), paths, 'current', interval=.01))
    assert cleaned == [True]


def test_local_status_tolerates_corrupt_state_without_signalling_pids(tmp_path):
    paths = RuntimePaths(tmp_path / 'qq')
    paths.home.mkdir()
    paths.state.write_text('{"manager_pid":1,broken', encoding='utf-8')
    assert local_status(paths)['manager_state'] == 'not_running'
    assert stop(paths) == 0


def test_managed_worker_exits_after_its_owner_process_dies(tmp_path):
    paths = RuntimePaths(tmp_path / 'qq')
    command = fake_command(tmp_path, lease_seconds=2)
    owner = tmp_path / 'fake_owner.py'
    owner.write_text(f'''
import os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
from qq_runtime import RuntimePaths, atomic_json, locked
paths = RuntimePaths(Path({str(paths.home)!r}))
token = 'owner-process'
atomic_json(paths.state, {{'run_id': token, 'heartbeat': time.time()}})
with open(os.devnull, 'wb') as output:
    child = subprocess.Popen({command!r} + ['--managed-token', token], stdout=output, stderr=output,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    for _ in range(100):
        atomic_json(paths.state, {{'run_id': token, 'heartbeat': time.time()}})
        if locked(paths.worker_lock):
            os._exit(17)
        time.sleep(.02)
os._exit(18)
''', encoding='utf-8')
    owner_process = subprocess.Popen([sys.executable, str(owner)], creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    assert owner_process.wait(timeout=8) == 17
    wait_for(lambda: not locked(paths.worker_lock), timeout=6)
    assert attempts(paths) == 1
