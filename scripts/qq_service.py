"""QQ supervisor: python scripts/qq_service.py start|status|stop|restart.

Process identity comes from OS locks and run tokens, never a recycled PID.
"""
import argparse
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen

from qq_runtime import FileLock, LockBusy, RuntimePaths, atomic_json, locked, read_json, stopping

ROOT = Path(__file__).resolve().parents[1]
PATHS = RuntimePaths(ROOT / '.local/qq')


def utc():
    return datetime.now(timezone.utc).isoformat()


class Supervisor:
    def __init__(self, paths=PATHS, command=None, poll=.5, base_delay=2, max_failures=5, stable_after=60):
        self.paths, self.command = paths, command or [sys.executable, '-X', 'utf8', '-u', str(ROOT / 'scripts/start_qq.py')]
        self.poll, self.base_delay = poll, base_delay
        self.max_failures, self.stable_after = max_failures, stable_after
        self.run_id = uuid.uuid4().hex
        self.state = {'run_id': self.run_id, 'manager_pid': os.getpid(), 'worker_pid': None,
                      'state': 'starting', 'started_at': utc(), 'last_exit': None, 'restarts': 0}
        self.log = logging.getLogger('qq-service-' + self.run_id)
        self.log.setLevel(logging.INFO)
        self.log.propagate = False

    def publish(self, state, **values):
        self.state.update(state=state, heartbeat=time.time(), **values)
        atomic_json(self.paths.state, self.state)

    def event(self, event, **values):
        self.log.info(json.dumps({'event': event, **values}, ensure_ascii=False))

    def wait(self, seconds, state):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if stopping(self.paths, self.run_id):
                return False
            self.publish(state)
            time.sleep(min(self.poll, max(0, deadline - time.monotonic())))
        return not stopping(self.paths, self.run_id)

    def run(self):
        self.paths.home.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(self.paths.manager_lock):
                handler = RotatingFileHandler(self.paths.home / 'service.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')
                handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
                self.log.addHandler(handler)
                try:
                    return self.loop()
                finally:
                    self.log.removeHandler(handler)
                    handler.close()
        except LockBusy:
            return 2

    def loop(self):
        self.publish('starting')
        if locked(self.paths.worker_lock):
            self.publish('external_worker')
            self.event('external_worker_detected')
            return 2
        failures = 0
        while not stopping(self.paths, self.run_id):
            self.publish('starting')
            # Rotate only between launches, when the worker has closed this file.
            gateway_log = self.paths.home / 'gateway.log'
            if gateway_log.exists() and gateway_log.stat().st_size > 2_000_000:
                try:
                    for index in (3, 2, 1):
                        source = gateway_log if index == 1 else gateway_log.with_name(f'gateway.log.{index - 1}')
                        if source.exists():
                            source.replace(gateway_log.with_name(f'gateway.log.{index}'))
                except OSError as exc:
                    self.event('log_rotation_deferred', error_type=type(exc).__name__)
            started = time.monotonic()
            try:
                with gateway_log.open('ab') as output:
                    child = subprocess.Popen([*self.command, '--managed-token', self.run_id], cwd=ROOT,
                                             env=dict(os.environ, PYTHONUTF8='1'), stdout=output, stderr=output,
                                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except OSError as exc:
                self.event('launch_failed', error_type=type(exc).__name__)
                self.publish('blocked', reason='launch_failed')
                return 1
            self.publish('running', worker_pid=child.pid)
            self.event('worker_started', worker_pid=child.pid)
            while child.poll() is None:
                requested = stopping(self.paths, self.run_id)
                self.publish('stopping' if requested else 'running')
                time.sleep(self.poll)
            code = child.returncode
            uptime = time.monotonic() - started
            self.publish('stopping' if stopping(self.paths, self.run_id) else 'restarting', worker_pid=None,
                         last_exit={'code': code, 'at': utc(), 'uptime_seconds': round(uptime, 2)})
            self.event('worker_exited', exit_code=code, uptime_seconds=round(uptime, 2))
            if stopping(self.paths, self.run_id):
                break
            failures = 1 if uptime >= self.stable_after else failures + 1
            if code == 2 or failures >= self.max_failures:
                reason = 'configuration_or_duplicate_worker' if code == 2 else 'repeated_failures'
                self.publish('blocked', reason=reason)
                self.event('automatic_restart_blocked', reason=reason)
                return 1
            delay = min(self.base_delay * 2 ** (failures - 1), 30)
            self.publish('restarting', restarts=self.state['restarts'] + 1, retry_in_seconds=delay)
            self.event('restart_scheduled', delay_seconds=delay)
            if not self.wait(delay, 'restarting'):
                break
        self.publish('stopped', worker_pid=None)
        self.event('stopped_by_request')
        return 0


def start(paths=PATHS):
    paths.home.mkdir(parents=True, exist_ok=True)
    if locked(paths.manager_lock):
        print('QQ守护已经运行，无需重复启动。')
        return 0
    if locked(paths.worker_lock):
        previous = read_json(paths.state)
        token = previous.get('run_id', '')
        if previous.get('worker_pid') and re.fullmatch('[0-9a-f]{32}', str(token)):
            # Ask only the old managed worker to stop; a manual worker ignores this.
            atomic_json(paths.stop, {'run_id': token, 'requested_at': utc()})
            deadline = time.monotonic() + 8
            while locked(paths.worker_lock) and time.monotonic() < deadline:
                time.sleep(.2)
        if locked(paths.worker_lock):
            print('检测到未由当前守护管理的QQ进程，请先停止原进程；未重复启动。')
            return 2
    with (paths.home / 'service-bootstrap.log').open('ab') as output:
        child = subprocess.Popen([sys.executable, '-X', 'utf8', '-u', str(Path(__file__).resolve()), 'run'], cwd=ROOT,
                                 stdout=output, stderr=output,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if locked(paths.manager_lock) and read_json(paths.state).get('heartbeat', 0) > time.time() - 5:
            print('QQ守护已启动，正在连接。运行 status 查看真实通道状态。')
            return 0
        if child.poll() is not None:
            break
        time.sleep(.2)
    print('守护未就绪，请运行 status 并检查 .local/qq/service.log。')
    return 1


def stop(paths=PATHS, timeout=20):
    if not locked(paths.manager_lock):
        if locked(paths.worker_lock):
            previous = read_json(paths.state)
            token = previous.get('run_id', '')
            if previous.get('worker_pid') and re.fullmatch('[0-9a-f]{32}', str(token)):
                atomic_json(paths.stop, {'run_id': token, 'requested_at': utc()})
                deadline = time.monotonic() + timeout
                while locked(paths.worker_lock) and time.monotonic() < deadline:
                    time.sleep(.2)
            if locked(paths.worker_lock):
                print('守护未运行，检测到外部QQ进程；未按旧PID强制结束。')
                return 2
        print('QQ守护已停止。')
        return 0
    state = read_json(paths.state)
    token = state.get('run_id', '')
    if not re.fullmatch('[0-9a-f]{32}', str(token)):
        print('守护启动状态尚未就绪，请稍后重试停止。')
        return 1
    atomic_json(paths.stop, {'run_id': token, 'requested_at': utc()})
    deadline = time.monotonic() + timeout
    while locked(paths.manager_lock) and time.monotonic() < deadline:
        time.sleep(.2)
    if locked(paths.manager_lock):
        print('已请求停止，正在等待当前任务退出；不会再自动拉起。请运行 status 查看。')
        return 1
    print('QQ守护和通道已停止，不会自动重新启动。')
    return 0


def local_status(paths=PATHS):
    state = read_json(paths.state)
    managed, worker = locked(paths.manager_lock), locked(paths.worker_lock)
    heartbeat = state.get('heartbeat', 0)
    age = time.time() - heartbeat if isinstance(heartbeat, (int, float)) else float('inf')
    return {'manager_running': managed, 'worker_running': worker,
              'manager_state': (state.get('state', 'unknown') if -5 <= age <= 5 else 'unresponsive') if managed else 'not_running',
              'last_exit': state.get('last_exit'), 'restarts': state.get('restarts', 0), 'last_stop_reason': state.get('reason')}


def status(paths=PATHS):
    result = local_status(paths)
    worker = result['worker_running']
    sys.path.insert(0, str(ROOT / 'backend'))
    try:
        from app.db import engine
        from app.models import ChannelRuntime, now
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        check_engine = create_engine(engine.url, hide_parameters=True, connect_args={'connect_timeout': 3, 'read_timeout': 3, 'write_timeout': 3})
        try:
            with Session(check_engine) as db:
                row = db.get(ChannelRuntime, 'qq')
                result['database'] = 'ok'
                result['qq_state'] = row.state if row and worker and -5 <= (now() - row.heartbeat_at).total_seconds() <= 20 else 'offline' if row else 'unknown'
                result['last_connected_at'] = row.connected_at.isoformat() + 'Z' if row and row.connected_at else None
        finally:
            check_engine.dispose()
    except Exception as exc:
        result.update(database='unavailable', qq_state='unknown', database_error_type=type(exc).__name__)
    try:
        with urlopen('http://127.0.0.1:8000/api/health', timeout=3) as response:
            result['api'] = 'ok' if response.status == 200 else 'unavailable'
    except Exception:
        result['api'] = 'unavailable'
    return result


def main():
    parser = argparse.ArgumentParser(description='QQ通道一键启动、检查、停止与异常恢复')
    parser.add_argument('action', choices=('start', 'status', 'stop', 'restart', 'run'))
    parser.add_argument('--json', action='store_true', help='状态检查输出JSON')
    args = parser.parse_args()
    if args.action == 'status':
        result = status()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            labels = {'starting': '启动中', 'running': '运行中', 'stopping': '正在停止', 'restarting': '等待恢复', 'not_running': '未运行', 'unresponsive': '心跳异常', 'unknown': '未知', 'blocked': '恢复已暂停', 'connected': '已连接', 'connecting': '连接中', 'reconnecting': '重连中', 'stopped': '已停止', 'offline': '已离线', 'ok': '正常', 'unavailable': '暂不可用'}
            print('QQ守护：' + labels.get(result['manager_state'], result['manager_state']))
            print('QQ进程：' + ('运行中' if result['worker_running'] else '未运行'))
            print('QQ连接：' + labels.get(result['qq_state'], result['qq_state']))
            print('数据库：' + labels.get(result['database'], result['database']))
            print('网页API：' + labels.get(result['api'], result['api']))
            print('最近连接：' + str(result.get('last_connected_at') or '暂无记录'))
            print('最近退出：' + json.dumps(result['last_exit'], ensure_ascii=False))
            if result['last_stop_reason']:
                reasons = {'configuration_or_duplicate_worker': '配置缺失或重复进程', 'repeated_failures': '连续退出，自动恢复已暂停', 'launch_failed': '进程启动失败'}
                print('暂停原因：' + reasons.get(result['last_stop_reason'], result['last_stop_reason']))
            print('日志目录：.local/qq（请勿分享本地配置或密钥）')
        return 0
    if args.action == 'start': return start()
    if args.action == 'stop': return stop()
    if args.action == 'restart':
        return start() if stop() == 0 else 1
    return Supervisor().run()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('操作已中断。已启动的守护可通过 stop 命令停止。')
    except Exception as exc:
        print(f'QQ服务操作失败（{type(exc).__name__}），请检查本地日志。')
        raise SystemExit(1)
