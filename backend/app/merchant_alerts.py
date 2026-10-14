"""推送接入准备：持久化转人工事件，不伪装成已发送的手机通知。"""
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .db import get_db
from .models import MerchantAlert, Handoff, Conversation, now
from .security import session_auth

router = APIRouter(prefix='/api/merchant/notifications')
ALERT_TTL = timedelta(minutes=10)


def record_handoff_alert(db, handoff):
    # 与交接共用事务：交接回滚时提醒也回滚；同一交接主键只允许一条记录。
    if db.get(MerchantAlert, handoff.id) is None:
        created = now()
        db.add(MerchantAlert(handoff_id=handoff.id, created_at=created,
                             expires_at=created + ALERT_TTL))
        db.flush()


def eligible_alerts(at=None):
    """未来发送器必须重新检查接手/结束/有效期，不能重放全部历史事件。"""
    return (select(MerchantAlert).join(Handoff, Handoff.id == MerchantAlert.handoff_id)
            .join(Conversation, Conversation.id == Handoff.conversation_id)
            .where(Conversation.status == 'waiting_human', Handoff.taken_at.is_(None),
                   Handoff.ended_at.is_(None), MerchantAlert.expires_at > (at or now())))


@router.get('/status')
def notification_status(request: Request, db: Session = Depends(get_db)):
    session_auth(request, db, merchant=True)
    count = db.scalar(select(func.count()).select_from(eligible_alerts().subquery()))
    return {'state': 'not_connected', 'provider': None, 'pending_events': count,
            'background_push_available': False,
            'detail': '转人工提醒事件已记录；尚未接通手机推送，不能收到锁屏通知。'}
