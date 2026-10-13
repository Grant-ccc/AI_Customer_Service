"""QQ 会话与网页工作台共用业务模型；仅通过官方网关接受消息。"""
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal
from .models import Conversation, Message, QQBinding, QQDelivery, now

FAILURE_PREFIX = '【QQ发送失败】'


def enqueue_delivery(db, c, message):
    if message.role == 'user' or message.content.startswith(FAILURE_PREFIX):
        return
    binding = db.scalar(select(QQBinding).where(QQBinding.conversation_id == c.id))
    if not binding:
        return
    count = db.scalar(select(func.count()).select_from(QQDelivery).where(
        QQDelivery.binding_id == binding.id, QQDelivery.reply_id == binding.reply_id))
    db.add(QQDelivery(message_id=message.id, binding_id=binding.id,
        reply_id=binding.reply_id, reply_seq=count + 1,
        deadline=binding.received_at + timedelta(minutes=5 if binding.kind == 'group' else 60)))


def ingest(app_id, event, data):
    from .conversations import Send, add_message, send_message, open_handoff, latest_handoff
    from .simple_ai import enqueue
    from .handoff_summary import explicit_human_request
    if event not in ('C2C_MESSAGE_CREATE', 'GROUP_AT_MESSAGE_CREATE'):
        return None
    group = event == 'GROUP_AT_MESSAGE_CREATE'
    author = data.get('author') or {}
    user = author.get('member_openid' if group else 'user_openid') or author.get('id')
    target = data.get('group_openid') if group else user
    reply_id = data.get('id')
    if not all(isinstance(value, str) and 0 < len(value) <= limit for value, limit in
               ((user, 128), (target, 128), (reply_id, 256))):
        return None
    if author.get('bot'):
        return None
    kind = 'group' if group else 'private'
    binding_id = hashlib.sha256(f'{app_id}:{kind}:{target}:{user}'.encode()).hexdigest()
    client_id = uuid5(NAMESPACE_URL, f'qq:{app_id}:{event}:{target}:{reply_id}')
    try:
        received = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
        if received.tzinfo is None:
            return None
        # MySQL DATETIME has second precision here; normalize before comparisons
        # so rounding in storage cannot discard two messages sent in one second.
        received = received.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    except (KeyError, ValueError, TypeError):
        return None
    if received > now() + timedelta(minutes=1):
        return None
    text = data.get('content', '')
    if not isinstance(text, str):
        return None
    unsupported = bool(data.get('attachments') or data.get('msg_elements'))
    too_long = len(text.strip()) > 2000
    content = '【收到附件消息，当前接入仅支持文字】' if unsupported else text.strip()
    if too_long:
        content = '【收到超过2000字的消息，请拆分发送】'
    if not content:
        return None
    try:
        with SessionLocal() as db:
            binding = db.get(QQBinding, binding_id)
            if binding is None:
                c = Conversation()
                db.add(c)
                db.flush()
                binding = QQBinding(id=binding_id, conversation_id=c.id, kind=kind,
                    target=target, reply_id=reply_id, received_at=received)
                db.add(binding)
                db.flush()
            else:
                c = db.scalar(select(Conversation).where(
                    Conversation.id == binding.conversation_id).with_for_update())
                db.refresh(binding)
            if db.scalar(select(Message.id).where(Message.conversation_id == c.id,
                                                 Message.client_message_id == str(client_id))):
                return None
            # A replayed older event must not replace a newer reply window.
            if received < binding.received_at:
                return None
            binding.reply_id, binding.received_at = reply_id, received
            if not send_message(db, c, Send(content=content, client_message_id=client_id), 'user'):
                return None
            task_id, summary_id = None, None
            if unsupported or too_long:
                add_message(db, c, 'system', '当前QQ接入仅支持2000字以内的文字。图片、视频暂未保存，请先用文字描述；需要时私聊发送“转人工”。')
            elif group and (explicit_human_request(content) or any(word in content for word in
                    ('订单', '手机号', '手机尾号', '退款', '换货', '赔偿', '收货地址', '转人工'))):
                add_message(db, c, 'assistant', '请私聊机器人查询订单或处理售后，不要在群里发送订单、手机号和地址。')
            elif c.status == 'ai_ready' and explicit_human_request(content):
                h = open_handoff(db, c, latest_handoff(db, c, lock=True), 'QQ用户主动请求人工',
                                 '已申请人工接待，可以继续补充文字。')
                summary_id = h.id
            else:
                from .shipments import lookup_reply
                answer = lookup_reply(db, content, group=group) if c.status == 'ai_ready' else None
                if answer:
                    from .simple_ai import invalidate
                    invalidate(db, c)
                    add_message(db, c, 'assistant', answer)
                else:
                    task_id = enqueue(db, c)
                    if not task_id and c.status == 'ai_ready':
                        add_message(db, c, 'system', 'AI暂不可用，请私聊发送“转人工”联系商家。')
            db.commit()
            return task_id, summary_id, group
    except IntegrityError:
        # Duplicate creation/event race; gateway replay can safely try again.
        return None


def finish(result):
    if not result:
        return
    task_id, summary_id, group = result
    if task_id:
        from .simple_ai import run
        from .models import AiReply
        from .conversations import add_message
        run(task_id, group=group)
        with SessionLocal() as db:
            task = db.get(AiReply, task_id)
            if task and task.status in ('failed', 'limited'):
                c = db.scalar(select(Conversation).where(
                    Conversation.id == task.conversation_id).with_for_update())
                if c.status == 'ai_ready' and c.latest_user_sequence == task.user_sequence:
                    add_message(db, c, 'system', 'AI暂未能回复，请稍后重试，或私聊发送“转人工”。')
                    db.commit()
    if summary_id:
        from .handoff_summary import run_summary
        run_summary(summary_id)


def claim_delivery():
    with SessionLocal() as db:
        row = db.scalar(select(QQDelivery).where(QQDelivery.status == 'pending')
            .order_by(QQDelivery.deadline, QQDelivery.reply_seq).with_for_update(skip_locked=True).limit(1))
        if not row:
            return None
        binding, message = db.get(QQBinding, row.binding_id), db.get(Message, row.message_id)
        row.status = 'sending'
        db.commit()
        return {'id': row.message_id, 'kind': binding.kind, 'target': binding.target,
                'reply_id': row.reply_id, 'reply_seq': row.reply_seq,
                'deadline': row.deadline, 'content':
                    ({'system': '[系统消息]\n', 'assistant': '[智能回复]\n'}.get(message.role, '')
                     + message.content)}


def complete_delivery(message_id, status):
    from .conversations import add_message
    with SessionLocal() as db:
        row = db.get(QQDelivery, message_id)
        row.status = status
        if status != 'sent':
            message = db.get(Message, message_id)
            c = db.scalar(select(Conversation).where(
                Conversation.id == message.conversation_id).with_for_update())
            detail = {'expired': '回复窗口已过期，请让用户重新发消息。',
                      'quota': '本条用户消息可回复次数已用完，请让用户重新发消息。',
                      'failed': '平台拒绝发送，请检查机器人权限。',
                      'unknown': '发送结果不确定，未自动重发，请核对QQ消息。'}[status]
            add_message(db, c, 'system', FAILURE_PREFIX + detail)
        db.commit()


def recover_deliveries():
    with SessionLocal() as db:
        ids = list(db.scalars(select(QQDelivery.message_id).where(QQDelivery.status == 'sending')))
    for message_id in ids:
        complete_delivery(message_id, 'unknown')
