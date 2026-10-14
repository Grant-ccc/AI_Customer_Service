"""Mobile UI contract checks with isolated mock responses; no QQ messages or DB writes."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:5175'
OUT = Path(__file__).resolve().parents[1] / '.local' / 'mobile-ui'
OUT.mkdir(parents=True, exist_ok=True)
NOW = '2026-10-14T10:24:00+08:00'

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, device_scale_factor=1)
        page = context.new_page()
        errors, writes = [], []
        state = {'status': 'waiting_human', 'fail_send': False, 'fail_queue': False, 'logged_in': True}
        messages = [{'id': 'm1', 'sequence': 1, 'role': 'user', 'content': '这款可以定制图案吗？', 'created_at': NOW}]
        def detail():
            return {'id': 'qq-demo-001', 'channel': 'qq', 'status': state['status'], 'revision': len(messages) + (state['status'] != 'waiting_human'), 'sequence': len(messages), 'messages': messages, 'ai': {'enabled': False, 'status': 'idle', 'simulation_date': '2025-02-01'}, 'handoff': {'id': 'handoff-1', 'round': 1, 'reason': '客户申请人工', 'covered_sequence': 1, 'created_at': NOW, 'ended_at': NOW if state['status'] == 'ended' else None, 'summary_status': 'pending', 'summary': None, 'summary_start_sequence': None, 'summary_covered_sequence': None}}
        def route_api(route):
            path = route.request.url.split('/api')[-1]
            status, payload = 200, {}
            if path == '/merchant/session':
                status, payload = (200, {'csrf': 'test-csrf'}) if state['logged_in'] else (401, {'detail': '请重新登录'})
            elif path == '/merchant/conversations':
                row = {'id': 'qq-demo-001', 'channel': 'qq', 'status': state['status'], 'preview': messages[-1]['content'], 'unread': True, 'updated_at': NOW}
                payload = {'pending': [] if state['status'] == 'ended' else [row], 'ended': [row] if state['status'] == 'ended' else []}
                if state['fail_queue']:
                    status, payload = 503, {'detail': '会话同步暂时中断'}
            elif path.endswith('/takeover'):
                assert route.request.headers.get('x-csrf-token') == 'test-csrf'
                state['status'] = 'human_active'
                payload = detail()
            elif path.endswith('/end'):
                state['status'] = 'ended'
                payload = detail()
            elif path.endswith('/messages'):
                body = route.request.post_data_json
                writes.append(body)
                assert route.request.headers.get('x-csrf-token') == 'test-csrf'
                if state['fail_send']:
                    status, payload = 503, {'detail': '测试断网，稍后重试'}
                else:
                    messages.append({'id': f'm{len(messages) + 1}', 'sequence': len(messages) + 1, 'role': 'merchant', 'content': body['content'], 'created_at': NOW, 'qq_delivery': 'sent'})
                    payload = detail()
            elif path.endswith('/read'):
                payload = {'ok': True}
            elif path == '/merchant/shipments':
                payload = {'count': 0, 'updated_at': None}
            elif path == '/merchant/logout':
                state['logged_in'] = False
            elif path.startswith('/merchant/conversations/'):
                payload = detail()
            else:
                raise AssertionError(f'Unexpected API request: {path}')
            route.fulfill(status=status, content_type='application/json', body=json.dumps(payload, ensure_ascii=False))
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/**', route_api)
        page.goto(BASE + '/merchant')
        page.wait_for_load_state('networkidle')
        nav = page.get_by_role('navigation', name='主导航')
        expect(page.locator('.mobile-contact')).to_have_count(1)
        page.screenshot(path=str(OUT / 'messages.png'))
        state['fail_queue'] = True
        expect(page.get_by_text('同步中断', exact=True)).to_be_visible(timeout=10000)
        expect(page.locator('.mobile-contact')).to_have_count(1)
        state['fail_queue'] = False
        expect(page.get_by_text('已同步', exact=True)).to_be_visible(timeout=10000)
        page.get_by_label('搜索会话').fill('不存在')
        expect(page.get_by_text('没有找到相关会话')).to_be_visible()
        page.get_by_label('清空搜索').click()
        page.get_by_role('button', name='未读', exact=True).click()
        expect(page.locator('.mobile-contact')).to_have_count(1)
        nav.get_by_role('button', name='工作台', exact=True).click()
        page.screenshot(path=str(OUT / 'work.png'))
        page.get_by_role('button', name='发货表管理', exact=True).click()
        expect(page.get_by_role('dialog')).to_be_visible()
        page.get_by_role('button', name='关闭', exact=True).click()
        page.locator('.mobile-contact').click()
        expect(nav).to_have_count(0)
        expect(page.get_by_label('消息内容')).to_be_disabled()
        expect(page.locator('.mobile-takeover').get_by_role('button', name='接手会话', exact=True)).to_be_enabled()
        page.get_by_role('button', name='交接详情', exact=True).click()
        expect(page.locator('.mobile-handoff-sheet')).to_be_visible()
        page.locator('.mobile-handoff-sheet').get_by_role('button', name='接手会话', exact=True).click()
        expect(page.get_by_role('button', name='已接手', exact=True)).to_be_disabled()
        page.get_by_role('button', name='关闭', exact=True).click()
        expect(page.get_by_label('消息内容')).to_be_enabled()
        page.get_by_label('消息内容').fill('可以的，请发一下您想定制的图案。')
        page.get_by_role('button', name='返回会话列表').click()
        expect(page.get_by_role('navigation', name='主导航')).to_be_visible()
        page.locator('.mobile-contact').click()
        expect(page.get_by_label('消息内容')).to_have_value('可以的，请发一下您想定制的图案。')
        state['fail_send'] = True
        page.get_by_role('button', name='发送', exact=False).click()
        expect(page.locator('.composer [role=alert]')).to_contain_text('测试断网')
        page.get_by_role('button', name='返回会话列表').click()
        page.locator('.mobile-contact').click()
        expect(page.get_by_label('消息内容')).to_have_value('可以的，请发一下您想定制的图案。')
        state['fail_send'] = False
        page.get_by_role('button', name='发送', exact=False).click()
        expect(page.get_by_label('消息内容')).to_have_value('')
        assert writes[0]['client_message_id'] == writes[1]['client_message_id'], 'Retries must reuse the idempotency key'
        assert len(writes) == 2 and len(messages) == 2, f'Unexpected sends: {writes!r}; messages: {messages!r}'
        expect(page.locator('.history-wrap')).to_have_count(1)
        expect(page.locator('.bubble')).to_have_count(2)
        expect(page.get_by_text('QQ · 平台已接受')).to_be_visible()
        page.screenshot(path=str(OUT / 'chat.png'))
        page.get_by_role('button', name='交接详情', exact=True).click()
        page.screenshot(path=str(OUT / 'handoff.png'))
        page.get_by_role('button', name='结束本次处理', exact=True).click()
        page.keyboard.press('Escape')
        expect(page.get_by_label('消息内容')).to_be_disabled()
        page.go_back()
        expect(page.get_by_role('navigation', name='主导航')).to_be_visible()
        page.get_by_role('button', name='已结束', exact=True).click()
        expect(page.locator('.mobile-contact')).to_have_count(1)
        for width, height in [(320, 640), (360, 800), (430, 932), (768, 1024)]:
            page.set_viewport_size({'width': width, 'height': height})
            expect(page.get_by_role('navigation', name='主导航')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}'
        page.set_viewport_size({'width': 1440, 'height': 950})
        expect(page.locator('.workspace')).to_be_visible()
        page.locator('.queue-tabs').get_by_role('button', name='已结束', exact=False).click()
        page.locator('.queue-item').click()
        expect(page.get_by_label('消息内容')).to_be_disabled()
        page.screenshot(path=str(OUT / 'desktop.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        if page.locator('.mobile-chat-toolbar').count():
            page.get_by_role('button', name='返回会话列表').click()
        page.get_by_role('navigation').get_by_role('button', name='我的', exact=True).click()
        page.screenshot(path=str(OUT / 'me.png'))
        page.get_by_role('button', name='退出登录', exact=True).click()
        expect(page.get_by_role('heading', name='登录工作台')).to_be_visible()
        assert not errors, errors
        print('PASS: mobile navigation, search, filter, shipment access, takeover, draft retention, send/retry, delivery status, end, browser back, 320–768px layouts, desktop and logout. Mock API only; no live QQ or device testing.')
        browser.close()

if __name__ == '__main__':
    run()
