"""Real HTTP + isolated MySQL mobile browser checks; no real QQ/device traffic.

Start with scripts/start_dev.py --test-db. Never run against the normal server.
"""
import hashlib
import json
from pathlib import Path
import sys
import uuid

from playwright.sync_api import sync_playwright, expect
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.db import engine
from app.models import Merchant, Message, VisitorSession
from app.security import password_hash

BASE = 'http://localhost:5174'
OUT = ROOT / '.local/mobile-live'
OUT.mkdir(parents=True, exist_ok=True)
LABEL = '移动接待验收-' + uuid.uuid4().hex[:8]


def run():
    test_engine = create_engine(engine.url.set(database='umbrella_test'), hide_parameters=True)
    if test_engine.url.database == engine.url.database:
        raise RuntimeError('The test database must be separate from the application database')
    with Session(test_engine) as db:
        if not db.get(Merchant, 'merchant'):
            db.add(Merchant(username='merchant', password_hash=password_hash('integration-password')))
            db.commit()
    checks = []
    report = {'scope': 'real API and isolated MySQL in mobile Chromium; no real QQ or Android device', 'checks': checks,
              'not_tested': ['QQ client sending/receiving', 'vivo WebView', 'physical keyboard and system back', 'screen lock and Android background resume']}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        user_context = browser.new_context()
        staff_context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        user, staff = user_context.new_page(), staff_context.new_page()
        user_session = {}
        def capture_session(response):
            if response.url.endswith('/api/visitor/session') and response.status == 200:
                user_session.update(response.json())
        user.on('response', capture_session)
        js_errors = []
        for page in (user, staff):
            page.on('pageerror', lambda error: js_errors.append(str(error)))
        try:
            user.goto(BASE)
            user.wait_for_load_state('networkidle')
            cookie = next(c for c in user_context.cookies() if c['name'] == 'umbrella_visitor')
            with test_engine.connect() as conn:
                assert conn.scalar(select(VisitorSession.token_hash).where(VisitorSession.token_hash == hashlib.sha256(cookie['value'].encode()).hexdigest())), 'Server is not using the isolated test DB'
            checks.append('Confirmed server session exists only in test database')
            def user_send(text):
                user.get_by_label('消息内容').fill(text)
                user.locator('.composer .primary').click()
                expect(user.get_by_label('消息内容')).to_have_value('')
                expect(user.locator('.bubble').filter(has_text=text)).to_have_count(1)
            user_send(LABEL + '：核实图案定制与制作时间')
            user.get_by_role('button', name='转人工', exact=True).click()
            expect(user.get_by_role('button', name='已申请人工')).to_be_disabled()
            conversation_id = user.request.get(BASE + '/api/visitor/conversation').json()['id']
            short_id = conversation_id[:8].upper()
            staff.goto(BASE + '/merchant')
            staff.wait_for_load_state('networkidle')
            staff.get_by_label('密码', exact=True).fill('integration-password')
            staff.get_by_role('button', name='进入工作台', exact=False).click()
            expect(staff.get_by_role('navigation', name='主导航')).to_be_visible()
            row = staff.locator('.mobile-contact').filter(has_text=short_id)
            expect(row).to_be_visible(timeout=12000)
            row.click()
            staff.locator('.mobile-takeover').get_by_role('button', name='接手会话', exact=True).click()
            expect(staff.get_by_label('消息内容')).to_be_enabled()
            expect(user.get_by_role('button', name='人工已接手')).to_be_disabled(timeout=12000)
            reply = LABEL + '：已接手，我们核实后回复。'
            staff.get_by_label('消息内容').fill(reply)
            staff.locator('.composer .primary').click()
            expect(staff.get_by_label('消息内容')).to_have_value('')
            expect(user.locator('.bubble').filter(has_text=reply)).to_have_count(1, timeout=12000)
            checks.append('Real browser login, handoff, takeover, merchant reply received in visitor browser')

            # Drop only the acknowledgment after the real API accepted the message.
            retry_text = LABEL + '：响应丢失时只保存一次'
            def lose_ack(route):
                response = route.fetch()
                assert response.ok
                route.abort('failed')
            staff.route('**/api/merchant/conversations/*/messages', lose_ack)
            staff.get_by_label('消息内容').fill(retry_text)
            staff.locator('.composer .primary').click()
            expect(staff.locator('.composer [role=alert]')).to_contain_text('连接暂时中断')
            expect(staff.get_by_label('消息内容')).to_have_value(retry_text)
            staff.unroute('**/api/merchant/conversations/*/messages', lose_ack)
            staff.get_by_role('button', name='重试提交', exact=False).click()
            expect(staff.get_by_label('消息内容')).to_have_value('')
            with test_engine.connect() as conn:
                assert conn.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id, Message.role == 'merchant', Message.content == retry_text)) == 1
            checks.append('Lost real API acknowledgment: same submission retried, exactly one row in MySQL')

            offline_text = LABEL + '：断网草稿'
            staff_context.set_offline(True)
            staff.get_by_label('消息内容').fill(offline_text)
            staff.locator('.composer .primary').click()
            expect(staff.locator('.composer [role=alert]')).to_contain_text('连接暂时中断')
            expect(staff.get_by_label('消息内容')).to_have_value(offline_text)
            staff.get_by_role('button', name='返回会话列表').click()
            staff_context.set_offline(False)
            row.click()
            expect(staff.get_by_label('消息内容')).to_have_value(offline_text)
            staff.locator('.composer .primary').click()
            expect(staff.get_by_label('消息内容')).to_have_value('')
            expect(user.locator('.bubble').filter(has_text=offline_text)).to_have_count(1, timeout=12000)
            checks.append('Offline send retains draft across back/reopen; reconnect submits successfully')

            for i in range(12):
                response = user.request.post(BASE + '/api/visitor/messages', headers={'X-CSRF-Token': user_session['csrf'], 'Origin': BASE}, data={'content': LABEL + f'历史{i}：' + '这是验收用长消息，检查聊天滚动。' * 12, 'client_message_id': str(uuid.uuid4())})
                assert response.ok
            expect(staff.locator('.bubble').filter(has_text=LABEL + '历史11：')).to_have_count(1, timeout=12000)
            staff.locator('.history').evaluate('(el) => { el.scrollTop = 0; el.dispatchEvent(new Event("scroll")); }')
            user_send(LABEL + '：查看历史时的新消息')
            expect(staff.get_by_role('button', name='有新消息', exact=False)).to_be_visible(timeout=12000)
            history_top = staff.locator('.history').evaluate('(el) => el.scrollTop')
            assert history_top < 60, 'New messages must not drag the reader away from history'
            queue = staff.request.get(BASE + '/api/merchant/conversations').json()
            assert next(r for r in queue['pending'] if r['id'] == conversation_id)['unread']
            staff.get_by_role('button', name='有新消息', exact=False).click()
            staff.wait_for_function("async (id) => { const r = await fetch('/api/merchant/conversations'); const q = await r.json(); return q.pending.find(x => x.id === id)?.unread === false; }", arg=conversation_id)
            checks.append('Long history scroll preserved; new-message indicator and real unread acknowledgment verified')

            staff.set_viewport_size({'width': 390, 'height': 420})
            staff.get_by_label('消息内容').fill(LABEL + '：缩小可视区域时的输入')
            composer = staff.locator('.composer').bounding_box()
            assert composer and composer['y'] >= 0 and composer['y'] + composer['height'] <= 421, 'Composer must fit a reduced viewport'
            staff.screenshot(path=str(OUT / 'reduced-viewport.png'))
            checks.append('Reduced 390x420 browser viewport keeps input and send button on screen (not a physical keyboard test)')
            staff.get_by_label('消息内容').fill('')
            staff.set_viewport_size({'width': 844, 'height': 390})
            expect(staff.get_by_role('button', name='返回会话列表')).to_be_visible()
            assert staff.evaluate('document.documentElement.scrollWidth <= innerWidth')
            staff.screenshot(path=str(OUT / 'landscape.png'))
            checks.append('Touch-screen landscape 844x390 retains mobile chat controls')
            staff.set_viewport_size({'width': 390, 'height': 844})

            other = staff_context.new_page()
            other.goto('about:blank')
            other.bring_to_front()
            if staff.evaluate('document.hidden'):
                user_send(LABEL + '：浏览器后台期间的新消息')
                staff.bring_to_front()
                expect(staff.locator('.bubble').filter(has_text=LABEL + '：浏览器后台期间的新消息')).to_have_count(1, timeout=12000)
                checks.append('Actual browser-tab background/foreground refresh (not Android background)')
            else:
                report['not_tested'].append('Browser visibility transition unavailable in this headless session')
            other.close()
            staff.get_by_role('button', name='结束处理', exact=True).click()
            staff.get_by_role('button', name='确认结束', exact=True).click()
            expect(user.locator('.status')).to_have_text('本次已结束', timeout=12000)
            expect(staff.get_by_label('消息内容')).to_be_disabled()
            user_send(LABEL + '：结束后再次咨询')
            user.get_by_role('button', name='转人工', exact=True).click()
            expect(staff.locator('.mobile-takeover')).to_be_visible(timeout=12000)
            detail = staff.request.get(BASE + '/api/merchant/conversations/' + conversation_id).json()
            assert detail['handoff']['round'] == 2
            staff.go_back()
            expect(staff.get_by_role('navigation', name='主导航')).to_be_visible()
            row.click()
            expect(staff.locator('.mobile-takeover')).to_be_visible()
            checks.append('End confirmed on real backend; new consultation creates second handoff; browser back/reopen works')
            staff.screenshot(path=str(OUT / 'second-handoff.png'))
            assert not js_errors, js_errors
            report['result'] = 'PASS for listed browser/API checks; real device and QQ-client checks remain unverified'
        except Exception as exc:
            report['result'] = 'FAIL'
            report['failure'] = type(exc).__name__ + ': ' + str(exc)
            staff.screenshot(path=str(OUT / 'failure.png'))
            raise
        finally:
            (OUT / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False))
            browser.close()
            test_engine.dispose()


if __name__ == '__main__':
    run()
