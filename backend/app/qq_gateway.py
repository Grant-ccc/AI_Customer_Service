"""单进程 QQ 官方 WebSocket 通道，业务与心跳分离。"""
import asyncio
from contextlib import suppress
import json
import logging
import time
from urllib.parse import quote

import httpx
from sqlalchemy import select
from websockets.asyncio.client import connect

from . import qq_bridge
from .db import SessionLocal
from .models import AiReply, Conversation, Handoff, HandoffSummary, QQBinding, now

log = logging.getLogger('qq')
API = 'https://api.bot.qq.com'


class PlatformError(Exception):
    """Provider bodies/credentials are deliberately excluded from exceptions."""


class QQClient:
    def __init__(self, app_id, secret, transport=None):
        self.app_id, self.secret = app_id, secret
        self.http = httpx.AsyncClient(timeout=20, follow_redirects=False, transport=transport)
        self.token, self.expires = '', 0
        self.token_lock = asyncio.Lock()
        self.session, self.seq = None, None
        self.connection_state, self.connected_at = 'connecting', None

    async def access_token(self):
        async with self.token_lock:
            if time.monotonic() >= self.expires:
                response = await self.http.post(API + '/app/getAppAccessToken',
                    json={'appId': self.app_id, 'clientSecret': self.secret})
                if response.status_code != 200:
                    raise PlatformError('凭据验证失败，请检查本地密钥和平台配置。')
                data = response.json()
                self.token = data.get('access_token', '')
                if not self.token:
                    raise PlatformError('平台未返回有效凭据。')
                self.expires = time.monotonic() + max(1, int(data['expires_in']) - 55)
            return self.token

    async def request(self, method, path, **kwargs):
        token = await self.access_token()
        return await self.http.request(method, API + path,
            headers={'Authorization': 'QQBot ' + token}, **kwargs)

    async def gateway(self):
        response = await self.request('GET', '/gateway/bot')
        if response.status_code != 200:
            raise PlatformError('获取QQ网关失败，请检查权限与IP白名单。')
        url = response.json().get('url', '')
        if not url.startswith('wss://'):
            raise PlatformError('平台未返回安全网关。')
        return url

    async def send_delivery(self, item):
        if item['deadline'] <= now():
            return 'expired'
        if item['reply_seq'] > (5 if item['kind'] == 'group' else 4):
            return 'quota'
        path = '/v2/' + ('groups/' if item['kind'] == 'group' else 'users/')
        path += quote(item['target'], safe='') + '/messages'
        try:
            response = await self.request('POST', path, json={
                'content': item['content'], 'msg_type': 0,
                'msg_id': item['reply_id'], 'msg_seq': item['reply_seq']})
            if response.status_code == 401:
                self.expires = 0
            if not response.is_success:
                return 'failed'
            return 'sent' if response.json().get('id') else 'unknown'
        except (httpx.HTTPError, ValueError, PlatformError):
            return 'unknown'

    async def receive(self):
        delay = 2
        while True:
            try:
                url = await self.gateway()
                async with connect(url, max_size=2**20, ping_interval=None) as ws:
                    hello = json.loads(await asyncio.wait_for(ws.recv(), 20))
                    if hello.get('op') != 10:
                        raise PlatformError('网关握手失败。')
                    token = 'QQBot ' + await self.access_token()
                    data = {'token': token, 'session_id': self.session, 'seq': self.seq} if self.session else {
                        'token': token, 'intents': 1 << 25, 'shard': [0, 1], 'properties': {}}
                    await ws.send(json.dumps({'op': 6 if self.session else 2, 'd': data}))
                    ack = True

                    async def heartbeat():
                        nonlocal ack
                        while True:
                            await asyncio.sleep(max(1, hello['d']['heartbeat_interval'] / 1000 * .8))
                            if not ack:
                                await ws.close()
                                return
                            ack = False
                            await ws.send(json.dumps({'op': 1, 'd': self.seq}))

                    heart = asyncio.create_task(heartbeat())
                    try:
                        async for raw in ws:
                            payload = json.loads(raw)
                            op = payload.get('op')
                            if op == 11:
                                ack = True
                            elif op == 1:
                                await ws.send(json.dumps({'op': 1, 'd': self.seq}))
                            elif op in (7, 9):
                                self.connection_state = 'reconnecting'
                                if op == 9:
                                    self.session, self.seq = None, None
                                break
                            elif op == 0:
                                event, body = payload.get('t'), payload.get('d') or {}
                                if event in ('READY', 'RESUMED'):
                                    if event == 'READY':
                                        self.session = body['session_id']
                                    self.connection_state, self.connected_at = 'connected', now()
                                    delay = 2
                                    log.info('QQ机器人已连接，等待私聊或群内@消息。')
                                if event in ('C2C_MESSAGE_CREATE', 'GROUP_AT_MESSAGE_CREATE'):
                                    await asyncio.to_thread(qq_bridge.ingest, self.app_id, event, body)
                                # Only advance after the event transaction has completed.
                                self.seq = payload.get('s', self.seq)
                    finally:
                        self.connection_state = 'reconnecting'
                        heart.cancel()
                        with suppress(asyncio.CancelledError):
                            await heart
            except asyncio.CancelledError:
                self.connection_state = 'stopped'
                raise
            except Exception as exc:
                self.connection_state = 'reconnecting'
                # Never log raw HTTP/WS exceptions: URLs or bodies may contain secrets.
                log.warning('QQ连接中断（%s），稍后重连。', type(exc).__name__)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def deliveries(self):
        while True:
            try:
                item = await asyncio.to_thread(qq_bridge.claim_delivery)
                if item:
                    status = await self.send_delivery(item)
                    await asyncio.to_thread(qq_bridge.complete_delivery, item['id'], status)
                    continue
            except Exception as exc:
                log.warning('QQ发送队列异常（%s）。', type(exc).__name__)
            await asyncio.sleep(1)


def business_step():
    with SessionLocal() as db:
        task = db.scalar(select(AiReply).join(QQBinding,
            QQBinding.conversation_id == AiReply.conversation_id)
            .where(AiReply.status == 'queued').order_by(AiReply.created_at).limit(1))
        if task:
            binding = db.scalar(select(QQBinding).where(QQBinding.conversation_id == task.conversation_id))
            result = (task.id, None, binding.kind == 'group')
        else:
            summary = db.scalar(select(HandoffSummary).join(Handoff,
                Handoff.id == HandoffSummary.handoff_id).join(QQBinding,
                QQBinding.conversation_id == Handoff.conversation_id)
                .where(HandoffSummary.status == 'queued').limit(1))
            result = (None, summary.handoff_id, False) if summary else None
    qq_bridge.finish(result)
    return bool(result)


async def business():
    while True:
        try:
            if await asyncio.to_thread(business_step):
                continue
        except Exception as exc:
            log.warning('QQ业务队列异常（%s）。', type(exc).__name__)
        await asyncio.sleep(1)


def recover_business():
    # Only recover this channel, never interrupt a running web visitor task.
    with SessionLocal() as db:
        tasks = db.scalars(select(AiReply).join(QQBinding,
            QQBinding.conversation_id == AiReply.conversation_id).where(AiReply.status == 'generating'))
        for task in tasks:
            task.status = 'failed'
            c = db.get(Conversation, task.conversation_id)
            c.revision += 1
            from .conversations import add_message
            add_message(db, c, 'system', 'AI处理被中断，请重新提问，或私聊发送“转人工”。')
        summaries = db.scalars(select(HandoffSummary).join(Handoff,
            Handoff.id == HandoffSummary.handoff_id).join(QQBinding,
            QQBinding.conversation_id == Handoff.conversation_id)
            .where(HandoffSummary.status == 'generating'))
        for summary in summaries:
            summary.status = 'failed'
        db.commit()


async def persist_status(state, connected_at=None):
    from .channel_status import record_qq
    write = asyncio.create_task(asyncio.to_thread(record_qq, state, connected_at))
    try:
        await asyncio.shield(write)
    except asyncio.CancelledError:
        # Finish an in-flight heartbeat before writing "stopped" during shutdown.
        await asyncio.gather(write, return_exceptions=True)
        raise


async def serve(app_id, secret, check=False):
    client = QQClient(app_id, secret)
    reporter = None
    try:
        if not check:
            await persist_status('connecting')
            async def report():
                while True:
                    try:
                        await persist_status(client.connection_state, client.connected_at)
                    except Exception as exc:
                        log.warning('QQ状态记录失败（%s）。', type(exc).__name__)
                    await asyncio.sleep(5)
            reporter = asyncio.create_task(report())
        await client.gateway()
        if check:
            print('QQ凭据和网关访问检查通过（尚未测试消息收发）。')
            return
        recover_business()
        qq_bridge.recover_deliveries()
        jobs = [asyncio.create_task(client.receive()), asyncio.create_task(client.deliveries()),
                asyncio.create_task(business())]
        try:
            await asyncio.gather(*jobs)
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
    finally:
        if reporter:
            reporter.cancel()
            with suppress(asyncio.CancelledError):
                await reporter
        if not check:
            with suppress(Exception):
                await persist_status('stopped', client.connected_at)
        await client.http.aclose()
