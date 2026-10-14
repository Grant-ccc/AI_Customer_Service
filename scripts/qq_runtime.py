"""Local process locks and cooperative QQ shutdown; never signal a saved PID."""
import asyncio
from contextlib import suppress
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
import uuid


class LockBusy(Exception):
    pass


class FileLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise LockBusy() from None
        return self

    def __exit__(self, *_):
        if self.file:
            # Closing the descriptor releases the OS lock even after a crash.
            self.file.close()
            self.file = None


@dataclass
class RuntimePaths:
    home: Path

    @property
    def manager_lock(self): return self.home / 'service.lock'
    @property
    def worker_lock(self): return self.home / 'worker.lock'
    @property
    def state(self): return self.home / 'service.json'
    @property
    def stop(self): return self.home / 'stop-request.json'


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        deadline = time.monotonic() + 1
        while True:
            try:
                temporary.replace(path)
                break
            except PermissionError:
                # Windows readers may briefly hold the destination without delete sharing.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
    finally:
        temporary.unlink(missing_ok=True)


def locked(path):
    try:
        with FileLock(path):
            return False
    except LockBusy:
        return True


def stopping(paths, run_id):
    return read_json(paths.stop).get('run_id') == run_id


async def managed_run(coroutine, paths, run_id, lease_seconds=20, interval=.5):
    """Stop gracefully if requested or if the owning supervisor disappears."""
    async def watch():
        unreadable_until = time.monotonic() + lease_seconds
        while True:
            if stopping(paths, run_id):
                return 'requested'
            state = read_json(paths.state)
            heartbeat = state.get('heartbeat')
            age = time.time() - heartbeat if isinstance(heartbeat, (int, float)) else float('inf')
            if state.get('run_id') and state['run_id'] != run_id:
                return 'supervisor_lost'
            if state.get('run_id') == run_id and isinstance(heartbeat, (int, float)):
                if not -5 <= age <= lease_seconds:
                    return 'supervisor_lost'
                unreadable_until = time.monotonic() + max(0, lease_seconds - max(0, age))
            elif time.monotonic() >= unreadable_until:
                return 'supervisor_lost'
            await asyncio.sleep(interval)
    server = asyncio.create_task(coroutine)
    guard = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait((server, guard), return_when=asyncio.FIRST_COMPLETED)
        if server in done:
            return await server
        print('QQ通道收到停止请求。' if guard.result() == 'requested' else 'QQ守护心跳失效，安全停止通道。', flush=True)
    finally:
        for task in (server, guard):
            task.cancel()
        for task in (server, guard):
            with suppress(asyncio.CancelledError):
                await task
