r"""从仓库根目录运行：.venv\Scripts\python.exe -X utf8 scripts/start_qq.py"""
import argparse
import asyncio
import logging
import os
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from dotenv import load_dotenv
from qq_runtime import FileLock, LockBusy, RuntimePaths, managed_run


def main():
    parser = argparse.ArgumentParser(description='QQ官方机器人通道')
    parser.add_argument('--check', action='store_true', help='仅验证凭据与网关')
    parser.add_argument('--managed-token', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.managed_token and (args.check or not re.fullmatch('[0-9a-f]{32}', args.managed_token)):
        parser.error('守护参数无效')
    load_dotenv(ROOT / 'backend/.env')
    app_id, secret = os.getenv('QQ_APP_ID', '').strip(), os.getenv('QQ_APP_SECRET', '').strip()
    if not app_id or not secret:
        print('请在 backend/.env 本地填写 QQ_APP_ID 和 QQ_APP_SECRET；不要提交或分享密钥。')
        return 2
    # OS file lock is released on crash; prevent two local workers sending duplicates.
    lock_path = ROOT / '.local/qq/worker.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock = FileLock(lock_path)
        lock.__enter__()
    except LockBusy:
        print('QQ通道已有进程运行，请勿重复启动。')
        return 2
    try:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
        logging.getLogger('httpx').setLevel(logging.WARNING)
        from app.init_db import ensure_schema
        from app.db import engine
        from app.qq_gateway import serve
        try:
            if not args.check:
                ensure_schema(engine)
            coroutine = serve(app_id, secret, check=args.check)
            if args.managed_token:
                coroutine = managed_run(coroutine, RuntimePaths(ROOT / '.local/qq'), args.managed_token)
            asyncio.run(coroutine)
        except KeyboardInterrupt:
            print('QQ通道已停止。')
        except Exception as exc:
            print(f'QQ启动失败（{type(exc).__name__}），请检查本地配置、网络与数据库。')
            return 1
    finally:
        lock.__exit__(None, None, None)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
