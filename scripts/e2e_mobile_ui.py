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
        state = {'status': 'waiting_human', 'summary_status': 'ready', 'extra_rows': False, 'fail_send': False, 'fail_end': False, 'fail_queue': False, 'logged_in': True}
        messages = [{'id': 'm1', 'sequence': 1, 'role': 'user', 'content': '这款可以定制图案吗？', 'created_at': NOW}]
        def detail():
            fact = lambda text: {'text': text, 'sequence': 1, 'quote': '验收用依据原文：' + text}
            summary = {'request': [fact('确认能否定制图案'), fact('核实制作时间'), fact('补充诉求。' * 30)], 'known': [fact('客户尚未提供图案')], 'attempted': [fact('已询问定制需求')], 'materials': []}
            return {'id': 'qq-demo-001', 'channel': 'qq', 'status': state['status'], 'revision': len(messages) + (state['status'] != 'waiting_human'), 'sequence': len(messages), 'messages': messages, 'ai': {'enabled': False, 'status': 'idle', 'simulation_date': '2025-02-01'}, 'handoff': {'id': 'handoff-1', 'round': 1, 'reason': '客户申请人工。' * 80, 'covered_sequence': 1, 'created_at': NOW, 'ended_at': NOW if state['status'] == 'ended' else None, 'summary_status': state['summary_status'], 'summary': summary if state['summary_status'] == 'ready' else None, 'summary_start_sequence': 1, 'summary_covered_sequence': 1}}
        def route_api(route):
            path = route.request.url.split('/api')[-1]
            status, payload = 200, {}
            if path == '/merchant/session':
                status, payload = (200, {'csrf': 'test-csrf'}) if state['logged_in'] else (401, {'detail': '请重新登录'})
            elif path == '/merchant/conversations':
                row = {'id': 'qq-demo-001', 'channel': 'qq', 'status': state['status'], 'preview': messages[-1]['content'], 'unread': True, 'updated_at': NOW}
                payload = {'pending': [] if state['status'] == 'ended' else [row], 'ended': [row] if state['status'] == 'ended' else []}
                if state['extra_rows']:
                    for extra_id, extra_status in [('waiting-demo', 'waiting_human'), ('active-demo', 'human_active'), ('ended-demo', 'ended')]:
                        extra = dict(row, id=extra_id, status=extra_status, unread=False, preview=extra_id)
                        payload['ended' if extra_status == 'ended' else 'pending'].append(extra)
                if state['fail_queue']:
                    status, payload = 503, {'detail': '会话同步暂时中断'}
            elif path.endswith('/takeover'):
                assert route.request.headers.get('x-csrf-token') == 'test-csrf'
                state['status'] = 'human_active'
                payload = detail()
            elif path.endswith('/end'):
                if state['fail_end']:
                    status, payload = 503, {'detail': '测试结束失败'}
                else:
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
        state['extra_rows'] = True
        expect(page.locator('.mobile-contact')).to_have_count(4, timeout=10000)
        page.get_by_role('button', name='待接手', exact=True).click()
        expect(page.locator('.mobile-contact')).to_have_count(2)
        expect(page.locator('.contact-status').first).to_have_text('待接手')
        page.get_by_role('button', name='接待中', exact=True).click()
        expect(page.locator('.mobile-contact')).to_have_count(1)
        expect(page.locator('.mobile-contact')).to_contain_text('active-demo')
        page.get_by_role('button', name='已结束', exact=True).click()
        expect(page.locator('.mobile-contact')).to_have_count(1)
        expect(page.locator('.mobile-contact')).to_contain_text('ended-demo')
        nav.get_by_role('button', name='工作台', exact=True).click()
        expect(page.locator('.mobile-stats button').first).to_contain_text('待接手2')
        expect(page.locator('.mobile-stats button').nth(1)).to_contain_text('接待中1')
        expect(page.locator('.mobile-task-list .mobile-contact').first.locator('.contact-status')).to_have_text('待接手')
        page.screenshot(path=str(OUT / 'work-mixed.png'))
        page.locator('.mobile-stats button').nth(1).click()
        expect(page.get_by_role('button', name='接待中', exact=True)).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('.mobile-contact')).to_have_count(1)
        page.get_by_label('搜索会话').fill('waiting-demo')
        expect(page.get_by_text('没有找到相关会话')).to_be_visible()
        page.get_by_role('button', name='待接手', exact=True).click()
        expect(page.locator('.mobile-contact')).to_have_count(1)
        page.get_by_label('清空搜索').click()
        page.get_by_role('button', name='全部', exact=True).click()
        state['extra_rows'] = False
        expect(page.locator('.mobile-contact')).to_have_count(1, timeout=10000)
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
        expect(page.get_by_label('消息内容')).to_be_hidden()
        expect(page.locator('.mobile-takeover').get_by_role('button', name='接手会话', exact=True)).to_be_enabled()
        takeover_box = page.locator('.mobile-takeover button').bounding_box()
        assert takeover_box and takeover_box['y'] + takeover_box['height'] <= 844, 'Takeover must be visible in chat without scrolling'
        page.get_by_role('button', name='交接详情', exact=True).click()
        expect(page.locator('.mobile-handoff-sheet')).to_be_visible()
        expect(page.get_by_role('heading', name='客户想解决什么')).to_be_visible()
        expect(page.locator('.summary-evidence blockquote').first).to_be_hidden()
        assert page.locator('.summary-fold[open], .handoff-metadata[open], .more-requests[open]').count() == 0
        page.locator('.request-overview .summary-evidence summary').first.click()
        expect(page.locator('.request-overview blockquote').first).to_be_visible()
        page.locator('.request-overview .summary-evidence summary').first.click()
        page.locator('.more-requests > summary').click()
        expect(page.locator('.more-requests .fact-full-text')).to_be_visible()
        page.locator('.more-requests .fact-full-text > summary').click()
        expect(page.locator('.more-requests .fact-full-text')).to_have_attribute('open', '')
        page.locator('.more-requests > summary').click()
        page.locator('.summary-fold > summary').first.click()
        expect(page.get_by_text('客户尚未提供图案', exact=True)).to_be_visible()
        page.locator('.summary-fold > summary').first.click()
        page.locator('.handoff-metadata > summary').click()
        expect(page.get_by_text('仅整理本轮第 1—1 条消息。', exact=True)).to_be_visible()
        page.locator('.handoff-metadata > summary').click()
        for summary_status, text in [('generating', '正在整理摘要…'), ('failed', '摘要生成失败，请查看完整记录。')]:
            state['summary_status'] = summary_status
            expect(page.get_by_text(text, exact=True)).to_be_visible(timeout=10000)
            expect(page.get_by_text('为什么转人工', exact=True)).to_be_visible()
        state['summary_status'] = 'ready'
        expect(page.get_by_role('heading', name='客户想解决什么')).to_be_visible()
        expect(page.get_by_text('确认能否定制图案', exact=True)).to_be_visible(timeout=10000)
        page.locator('.sheet-body').evaluate('(el) => { el.scrollTop = 0; }')
        page.screenshot(path=str(OUT / 'handoff-compact.png'))
        page.locator('.handoff-reason > summary').click()
        close_button = page.locator('.mobile-handoff-sheet .sheet-title').get_by_role('button', name='关闭', exact=True)
        body = page.get_by_role('region', name='交接详情正文')
        for width, height in [(390, 844), (360, 640)]:
            page.set_viewport_size({'width': width, 'height': height})
            body.evaluate('(el) => { el.scrollTop = 0; }')
            before = close_button.bounding_box()
            body.hover()
            page.mouse.wheel(0, 2000)
            page.wait_for_function("document.querySelector('.sheet-body').scrollTop > 100")
            page.wait_for_function("Math.abs(document.querySelector('.sheet-body').scrollHeight - document.querySelector('.sheet-body').clientHeight - document.querySelector('.sheet-body').scrollTop) < 2")
            after = close_button.bounding_box()
            assert before and after and abs(before['y'] - after['y']) < 1, 'Close must not move while the actual detail body scrolls'
            assert page.locator('.mobile-handoff-sheet').evaluate('(el) => el.scrollTop') == 0, 'The dialog shell must not scroll'
            assert close_button.evaluate('(el) => { const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); }'), 'Close must remain exposed to touch'
        page.screenshot(path=str(OUT / 'handoff-scrolled.png'))
        close_button.click()
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('.mobile-takeover').get_by_role('button', name='接手会话', exact=True).click()
        page.get_by_role('button', name='交接详情', exact=True).click()
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
        expect(page.get_by_role('button', name='重试提交', exact=False)).to_be_enabled()
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
        for delivery, label in [('pending', '等待发送'), ('sending', '发送中'), ('failed', '发送失败'), ('expired', '窗口过期，未发送'), ('quota', '次数用尽，未发送'), ('unknown', '发送结果待核对')]:
            messages[-1]['qq_delivery'] = delivery
            expect(page.locator('.delivery-label')).to_have_text('QQ · ' + label, timeout=10000)
        expect(page.locator('.delivery-help')).to_contain_text('先到 QQ 核对')
        # Stub clipboard writes so the check never changes the user's clipboard.
        page.evaluate("() => { navigator.clipboard.writeText = async () => { throw new Error('Clipboard unavailable'); }; }")
        page.get_by_role('button', name='复制回复', exact=True).click()
        expect(page.get_by_label('待复制的回复')).to_have_value(messages[-1]['content'])
        page.evaluate('() => { navigator.clipboard.writeText = async text => { window.__copiedReply = text; }; }')
        page.get_by_role('button', name='复制回复', exact=True).click()
        expect(page.get_by_role('button', name='已复制回复')).to_be_visible()
        assert page.evaluate('window.__copiedReply') == messages[-1]['content']
        assert len(writes) == 2, 'Copy and unknown delivery must not send or retry a QQ message'
        page.screenshot(path=str(OUT / 'delivery-attention.png'))
        messages[-1]['qq_delivery'] = 'sent'
        expect(page.locator('.delivery-label')).to_have_text('QQ · 平台已接受', timeout=10000)
        page.screenshot(path=str(OUT / 'chat.png'))
        page.get_by_role('button', name='交接详情', exact=True).click()
        page.screenshot(path=str(OUT / 'handoff.png'))
        page.keyboard.press('Escape')
        page.get_by_role('button', name='结束处理', exact=True).click()
        expect(page.get_by_role('dialog', name='结束本次人工接待？')).to_be_visible()
        page.get_by_role('button', name='继续接待', exact=True).click()
        assert state['status'] == 'human_active', 'Cancel must not end the conversation'
        page.get_by_role('button', name='结束处理', exact=True).click()
        state['fail_end'] = True
        page.get_by_role('button', name='确认结束', exact=True).click()
        expect(page.locator('.end-confirm-error')).to_contain_text('结束失败')
        assert state['status'] == 'human_active'
        state['fail_end'] = False
        page.get_by_role('button', name='确认结束', exact=True).click()
        expect(page.get_by_role('dialog', name='结束本次人工接待？')).to_be_hidden()
        expect(page.get_by_role('button', name='结束处理', exact=True)).to_have_count(0)
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
