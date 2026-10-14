"""Persist the local QQ worker heartbeat; no credentials or provider bodies."""
from datetime import timedelta, timezone
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from .db import SessionLocal, get_db
from .models import ChannelRuntime, now
from .security import session_auth

router = APIRouter(prefix='/api/merchant')
STALE_AFTER = timedelta(seconds=20)


def record_qq(state, connected_at=None):
    with SessionLocal() as db:
        row = db.get(ChannelRuntime, 'qq')
        if row is None:
            row = ChannelRuntime(name='qq')
            db.add(row)
        row.state = state
        row.heartbeat_at = now()
        if connected_at is not None:
            row.connected_at = connected_at
        db.commit()


def stamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


@router.get('/channels/qq')
def qq_status(request: Request, db: Session = Depends(get_db)):
    session_auth(request, db, merchant=True)
    row = db.get(ChannelRuntime, 'qq')
    checked = now()
    state = 'unknown'
    if row:
        fresh = checked - STALE_AFTER <= row.heartbeat_at <= checked + timedelta(seconds=5)
        state = {'connected': 'online', 'connecting': 'connecting', 'reconnecting': 'reconnecting', 'stopped': 'offline'}.get(row.state, 'unknown') if fresh else 'offline'
    return {'state': state, 'last_connected_at': stamp(row.connected_at) if row else None,
            'last_heartbeat_at': stamp(row.heartbeat_at) if row else None, 'checked_at': stamp(checked)}
