import asyncio
from datetime import timedelta
from fastapi.testclient import TestClient
import pytest
from app import channel_status, config, qq_gateway
from app.main import app
from app.models import ChannelRuntime, now


def merchant():
    client = TestClient(app)
    response = client.post('/api/merchant/login', headers={'Origin': config.APP_ORIGIN}, json={'username': 'merchant', 'password': 'integration-password'})
    assert response.status_code == 200
    return client


def test_status_requires_merchant_and_unknown_is_not_online(database):
    assert TestClient(app).get('/api/merchant/channels/qq').status_code == 401
    response = merchant().get('/api/merchant/channels/qq')
    assert response.json()['state'] == 'unknown'
    assert response.json()['last_connected_at'] is None
    assert response.headers['cache-control'] == 'no-store'
    assert set(response.json()) == {'state', 'last_connected_at', 'last_heartbeat_at', 'checked_at'}


@pytest.mark.parametrize('state,seconds,expected', [('connected', 0, 'online'), ('connected', 21, 'offline'), ('connected', -10, 'offline'), ('connecting', 0, 'connecting'), ('reconnecting', 0, 'reconnecting'), ('stopped', 0, 'offline')])
def test_fresh_heartbeat_and_stale_worker(database, monkeypatch, state, seconds, expected):
    clock = now()
    monkeypatch.setattr(channel_status, 'now', lambda: clock)
    with database() as db:
        db.add(ChannelRuntime(name='qq', state=state, heartbeat_at=clock - timedelta(seconds=seconds), connected_at=clock - timedelta(minutes=2)))
        db.commit()
    result = merchant().get('/api/merchant/channels/qq').json()
    assert result['state'] == expected
    assert result['last_connected_at'].endswith('+00:00')


def test_reconnect_and_restart_preserve_last_connection(database, monkeypatch):
    monkeypatch.setattr(channel_status, 'SessionLocal', database)
    connected = now()
    channel_status.record_qq('connected', connected)
    channel_status.record_qq('stopped')
    channel_status.record_qq('connecting')
    result = merchant().get('/api/merchant/channels/qq').json()
    assert result['state'] == 'connecting'
    assert result['last_connected_at'].startswith(connected.strftime('%Y-%m-%dT%H:%M:%S'))


def test_check_does_not_publish_status_and_failed_start_stops(monkeypatch):
    recorded = []
    monkeypatch.setattr(channel_status, 'record_qq', lambda state, connected=None: recorded.append(state))
    async def fail(_):
        raise qq_gateway.PlatformError('test failure')
    monkeypatch.setattr(qq_gateway.QQClient, 'gateway', fail)
    with pytest.raises(qq_gateway.PlatformError):
        asyncio.run(qq_gateway.serve('test-app', 'test-secret', check=True))
    assert recorded == []
    with pytest.raises(qq_gateway.PlatformError):
        asyncio.run(qq_gateway.serve('test-app', 'test-secret'))
    assert recorded[0] == 'connecting' and recorded[-1] == 'stopped'
