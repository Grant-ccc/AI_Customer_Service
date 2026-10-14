import asyncio
from datetime import timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app import config, qq_bridge, qq_gateway, simple_ai
from app.conversations import add_message, snapshot
from app.models import AiReply, Conversation, Handoff, Message, QQBinding, QQDelivery, now


@pytest.fixture
def qq(database, monkeypatch):
    monkeypatch.setattr(qq_bridge, 'SessionLocal', database)
    monkeypatch.setattr(qq_gateway, 'SessionLocal', database)
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    return database


def event(text='多少钱', user='alice', group=None, mid='m1', **extra):
    body = {'id': mid, 'content': text, 'timestamp': now().replace(tzinfo=timezone.utc).isoformat(),
            'author': {'user_openid': user, 'member_openid': user}, **extra}
    if group:
        body['group_openid'] = group
    return 'GROUP_AT_MESSAGE_CREATE' if group else 'C2C_MESSAGE_CREATE', body


def receive(**kwargs):
    return qq_bridge.ingest('test-app', *event(**kwargs))


def test_duplicate_event_and_isolation(qq):
    kind, data = event()
    first = qq_bridge.ingest('test-app', kind, data)
    assert qq_bridge.ingest('test-app', kind, data) is None
    qq_bridge.finish(first)
    receive(user='bob', mid='m2')
    receive(group='g1', mid='m3')
    receive(group='g1', user='bob', mid='m4')
    receive(group='g2', mid='m5')
    with qq() as db:
        assert len(list(db.scalars(select(QQBinding)))) == 5
        c = db.scalar(select(Conversation).join(QQBinding).where(QQBinding.kind == 'private', QQBinding.target == 'alice'))
        assert [m.role for m in db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.sequence))] == ['user', 'assistant']


def test_private_handoff_and_workbench_reply(qq):
    result = receive(text='转人工')
    qq_bridge.finish(result)
    with qq() as db:
        c = db.scalar(select(Conversation))
        assert c.status == 'waiting_human'
        h = db.scalar(select(Handoff))
        assert h.reason == 'QQ用户主动请求人工'
        c.status = 'human_active'
        add_message(db, c, 'merchant', '我来核实一下。')
        db.commit()
        state = snapshot(db, c, merchant=True)
        assert state['channel'] == 'qq'
        assert state['messages'][-1]['qq_delivery'] == 'pending'
    deliveries = []
    while item := qq_bridge.claim_delivery():
        deliveries.append(item)
        qq_bridge.complete_delivery(item['id'], 'sent')
    assert deliveries[-1]['content'] == '我来核实一下。'
    assert all(item['target'] == 'alice' for item in deliveries)
    assert [item['reply_seq'] for item in deliveries] == list(range(1, len(deliveries) + 1))


@pytest.mark.parametrize('delivery', ['pending', 'sending', 'sent', 'failed', 'unknown', 'expired', 'quota'])
def test_qq_needs_reply_until_platform_accepts(qq, delivery):
    qq_bridge.finish(receive(text='转人工'))
    with qq() as db:
        c = db.scalar(select(Conversation))
        c.status = 'human_active'
        reply = add_message(db, c, 'merchant', '我来核实。')
        db.flush()
        db.get(QQDelivery, reply.id).status = delivery
        db.commit()
        state = snapshot(db, c, merchant=True)
        assert state['needs_reply'] == (delivery != 'sent')
        assert state['end_checks']['qq_pending'] == int(delivery in ('pending', 'sending'))
        assert state['end_checks']['qq_failed'] == int(delivery in ('failed', 'expired', 'quota'))
        assert state['end_checks']['qq_unknown'] == int(delivery == 'unknown')
        c.status = 'ended'
        db.commit()
        assert not snapshot(db, c, merchant=True)['needs_reply']


@pytest.mark.parametrize('text', ['查订单', '我要申请退款', '转人工'])
def test_group_redirect_without_handoff(qq, text):
    qq_bridge.finish(receive(text=text, group='g1'))
    with qq() as db:
        assert db.scalar(select(Handoff)) is None
        assert '私聊' in db.scalar(select(Message).where(Message.role == 'assistant')).content


def test_group_model_transfer_cannot_create_handoff(qq, monkeypatch):
    monkeypatch.setattr(simple_ai, 'generate', lambda *_: simple_ai.TransferReply('敏感售后内容', '需商家核实'))
    qq_bridge.finish(receive(text='伞坏了', group='g1'))
    with qq() as db:
        assert db.scalar(select(Handoff)) is None
        answer = db.scalar(select(Message).where(Message.role == 'assistant')).content
        assert '私聊' in answer and '敏感售后内容' not in answer


def test_human_pause_and_new_reply_window(qq, monkeypatch):
    receive(text='转人工')
    calls = []
    monkeypatch.setattr(simple_ai, 'generate', lambda *_: calls.append(1) or 'AI')
    qq_bridge.finish(receive(text='补充描述', mid='m2'))
    with qq() as db:
        c = db.scalar(select(Conversation))
        assert c.status == 'waiting_human' and calls == []
        add_message(db, c, 'system', '商家已接手本次会话。')
        db.commit()
        rows = list(db.scalars(select(QQDelivery).order_by(QQDelivery.reply_id)))
        assert {row.reply_id for row in rows} == {'m1', 'm2'}
        assert rows[-1].reply_seq == 1


def test_unsupported_attachment_is_not_downloaded_or_summarized(qq):
    qq_bridge.finish(receive(text='看这个', attachments=[{'url': 'http://internal/secret'}]))
    with qq() as db:
        messages = list(db.scalars(select(Message).order_by(Message.sequence)))
        assert all('http://internal' not in m.content for m in messages)
        assert '暂未保存' in messages[-1].content
        assert db.scalar(select(AiReply)) is None


def test_failure_notices_do_not_loop_and_recovery_is_conservative(qq):
    receive(text='转人工')
    item = qq_bridge.claim_delivery()
    qq_bridge.recover_deliveries()
    with qq() as db:
        assert db.get(QQDelivery, item['id']).status == 'unknown'
        assert len(list(db.scalars(select(QQDelivery)))) == 1
        assert qq_bridge.FAILURE_PREFIX in list(db.scalars(select(Message).order_by(Message.sequence)))[-1].content
    assert qq_bridge.claim_delivery() is None


def test_replayed_old_and_non_at_events_ignored(qq):
    receive()
    kind, data = event(mid='older', timestamp=(now() - timedelta(minutes=1)).replace(tzinfo=timezone.utc).isoformat())
    assert qq_bridge.ingest('test-app', kind, data) is None
    assert qq_bridge.ingest('test-app', 'GROUP_MESSAGE_CREATE', data) is None
    with qq() as db:
        assert db.scalar(select(QQBinding)).reply_id == 'm1'


def test_two_messages_in_same_second_are_not_dropped(qq):
    stamp = now().replace(microsecond=900000, tzinfo=timezone.utc).isoformat()
    receive(timestamp=stamp)
    assert receive(mid='m2', text='再问一句', timestamp=stamp) is not None
    with qq() as db:
        assert db.scalar(select(QQBinding)).reply_id == 'm2'
        assert len(list(db.scalars(select(Message).where(Message.role == 'user')))) == 2


def test_web_restart_does_not_cancel_qq_jobs(qq):
    receive()
    simple_ai.recover()
    with qq() as db:
        assert db.scalar(select(AiReply)).status == 'queued'
    assert qq_gateway.business_step()


@pytest.mark.parametrize('mode', ['sent', 'failed', 'unknown', 'expired', 'quota'])
def test_platform_delivery_contract(mode):
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path.endswith('getAppAccessToken'):
            return httpx.Response(200, json={'access_token': 'local-test-token', 'expires_in': 7200})
        if mode == 'unknown':
            raise httpx.ReadTimeout('sensitive-provider-body')
        return httpx.Response(403 if mode == 'failed' else 200, json={'id': 'reply'})
    async def run():
        client = qq_gateway.QQClient('app', 'test-secret', httpx.MockTransport(handle))
        try:
            item = {'kind': 'private', 'target': 'alice', 'reply_id': 'm1',
                    'reply_seq': 5 if mode == 'quota' else 1,
                    'deadline': now() + timedelta(minutes=-1 if mode == 'expired' else 1),
                    'content': '测试回复'}
            assert await client.send_delivery(item) == mode
            if mode in ('expired', 'quota'):
                assert requests == []
            else:
                assert requests[-1].url.path == '/v2/users/alice/messages'
                assert b'"msg_id":"m1"' in requests[-1].content
                assert requests[-1].headers['authorization'] == 'QQBot local-test-token'
                assert len(requests) == 2  # uncertain sends are never automatically retried
        finally:
            await client.http.aclose()
    asyncio.run(run())


def test_token_cache_and_refresh():
    count = []
    def handle(request):
        count.append(1)
        return httpx.Response(200, json={'access_token': f'token-{len(count)}', 'expires_in': 7200})
    async def run():
        client = qq_gateway.QQClient('app', 'test-secret', httpx.MockTransport(handle))
        try:
            assert await asyncio.gather(client.access_token(), client.access_token()) == ['token-1', 'token-1']
            client.expires = 0
            assert await client.access_token() == 'token-2'
            assert len(count) == 2
        finally:
            await client.http.aclose()
    asyncio.run(run())


def test_websocket_identify_and_resume(monkeypatch):
    import json
    client = qq_gateway.QQClient('app', 'test-secret')
    sent, attempts = [], []
    class Socket:
        async def __aenter__(self):
            attempts.append(1)
            self.packets = iter([{'op': 0, 't': 'READY', 's': 7, 'd': {'session_id': 'session'}},
                                 {'op': 7}]) if len(attempts) == 1 else iter([])
            return self
        async def __aexit__(self, *args):
            pass
        async def recv(self):
            return json.dumps({'op': 10, 'd': {'heartbeat_interval': 45000}})
        async def send(self, data):
            sent.append(json.loads(data))
            if sent[-1]['op'] == 6:
                raise asyncio.CancelledError()
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                return json.dumps(next(self.packets))
            except StopIteration:
                raise StopAsyncIteration
    async def gateway():
        return 'wss://api.bot.qq.com/websocket/'
    async def token():
        return 'test-token'
    original_sleep = asyncio.sleep
    async def fast_sleep(delay):
        # Only accelerate reconnects; heartbeat must remain independently cancellable.
        await original_sleep(0 if delay <= 2 else 100)
    monkeypatch.setattr(client, 'gateway', gateway)
    monkeypatch.setattr(client, 'access_token', token)
    monkeypatch.setattr(qq_gateway, 'connect', lambda *args, **kwargs: Socket())
    monkeypatch.setattr(asyncio, 'sleep', fast_sleep)
    async def run():
        try:
            with pytest.raises(asyncio.CancelledError):
                await client.receive()
            assert sent[0]['op'] == 2 and sent[0]['d']['intents'] == 1 << 25
            assert sent[-1] == {'op': 6, 'd': {'token': 'QQBot test-token', 'session_id': 'session', 'seq': 7}}
        finally:
            await client.http.aclose()
    asyncio.run(run())
