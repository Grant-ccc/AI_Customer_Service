from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, qq_bridge, simple_ai
from app.conversations import open_handoff
from app.main import app
from app.merchant_alerts import eligible_alerts, record_handoff_alert
from app.models import Conversation, Handoff, MerchantAlert, now
from test_handoff import visitor, merchant, payload
from test_qq import event


def saved(factory):
    with factory() as db:
        return list(db.scalars(select(MerchantAlert)))


def test_repeated_and_concurrent_request_records_one_event(database):
    v, cid = visitor()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: v.post('/api/visitor/handoff').status_code,
                             range(2))) == [200, 200]
    v.post('/api/visitor/messages', json=payload('补充资料，不应重复提醒'))
    assert len(saved(database)) == 1
    with database() as db:
        alert = db.scalar(select(MerchantAlert))
        assert db.get(Handoff, alert.handoff_id).conversation_id == cid
        record_handoff_alert(db, db.get(Handoff, alert.handoff_id))
        db.commit()
    assert len(saved(database)) == 1


def test_alert_and_handoff_roll_back_together(database):
    with database() as db:
        c = Conversation()
        db.add(c)
        db.flush()
        open_handoff(db, c, None, '测试', '转人工')
        assert len(list(db.scalars(select(MerchantAlert)))) == 1
        db.rollback()
    assert saved(database) == []


def test_takeover_end_next_round_and_expiry(database):
    v, cid = visitor()
    assert v.post('/api/visitor/handoff').status_code == 200
    m = merchant()
    root = f'/api/merchant/conversations/{cid}'
    h = m.get(root).json()['handoff']['id']
    with database() as db:
        assert len(list(db.scalars(eligible_alerts()))) == 1
        expires = db.get(MerchantAlert, h).expires_at
        assert list(db.scalars(eligible_alerts(expires))) == []
    assert m.post(root + '/takeover', json={'handoff_id': h}).status_code == 200
    with database() as db:
        assert list(db.scalars(eligible_alerts())) == []
    assert m.post(root + '/end', json={'handoff_id': h}).status_code == 200
    assert v.post('/api/visitor/handoff').status_code == 200
    assert len(saved(database)) == 2
    with database() as db:
        active = list(db.scalars(eligible_alerts()))
        assert len(active) == 1 and active[0].handoff_id != h
        active[0].expires_at = now() - timedelta(seconds=1)
        db.commit()
        assert list(db.scalars(eligible_alerts())) == []


def test_qq_event_replay_records_one_alert(database, monkeypatch):
    monkeypatch.setattr(qq_bridge, 'SessionLocal', database)
    kind, body = event(text='转人工')
    assert qq_bridge.ingest('test-app', kind, body) is not None
    assert qq_bridge.ingest('test-app', kind, body) is None
    assert len(saved(database)) == 1


def test_model_transfer_uses_same_alert_entry(database, monkeypatch):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    monkeypatch.setattr(simple_ai, 'generate', lambda *_:
                        simple_ai.TransferReply('商家来协助处理。', '申请换货'))
    v, _ = visitor()
    assert v.post('/api/visitor/messages', json=payload('我想换一把')).status_code == 200
    assert len(saved(database)) == 1


def test_status_requires_merchant_and_never_claims_delivery(database):
    url = '/api/merchant/notifications/status'
    assert TestClient(app).get(url).status_code == 401
    v, _ = visitor()
    assert v.get(url).status_code == 401
    assert v.post('/api/visitor/handoff').status_code == 200
    response = merchant().get(url)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert body['state'] == 'not_connected'
    assert body['background_push_available'] is False
    assert body['provider'] is None and body['pending_events'] == 1
    assert set(body) == {'state', 'provider', 'pending_events',
                         'background_push_available', 'detail'}
