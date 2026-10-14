import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { shortId, statusLabel, time } from './api';
import type { Queue, Row, QqChannelStatus } from './api';
import ShipmentImport from './ShipmentImport';
import QqStatusPanel from './QqStatusPanel';

type Page = 'messages' | 'work' | 'me';
type IconName = 'message' | 'grid' | 'user' | 'search' | 'back' | 'arrow' | 'file' | 'clock';
export function MobileIcon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    message: <path d="M20 11a8 8 0 0 1-8 8H5l-3 3V11a9 9 0 0 1 18 0ZM7 9h8M7 13h5" />,
    grid: <><rect x="3" y="3" width="6" height="6" rx="1" /><rect x="15" y="3" width="6" height="6" rx="1" /><rect x="3" y="15" width="6" height="6" rx="1" /><rect x="15" y="15" width="6" height="6" rx="1" /></>,
    user: <><circle cx="12" cy="7" r="4" /><path d="M3 21v-2a9 9 0 0 1 18 0v2" /></>,
    search: <><circle cx="10" cy="10" r="7" /><path d="m15 15 6 6" /></>,
    back: <path d="m15 4-8 8 8 8" />,
    arrow: <path d="m9 5 7 7-7 7" />,
    file: <><path d="M5 3h10l4 4v14H5ZM14 3v5h5M8 12h8M8 16h6" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 6v6l4 2" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
export const contactName = (row: { channel?: string; id: string }) => `${row.channel === 'qq' ? 'QQ 用户' : '访客'} ${shortId(row.id)}`;
function Contact({ row, open }: { row: Row; open: (id: string) => void }) {
  return <button className="mobile-contact" onClick={() => open(row.id)}>
    <span className={`contact-avatar ${row.channel === 'qq' ? 'qq' : ''}`} aria-hidden="true">{row.channel === 'qq' ? 'Q' : '访'}</span>
    <span className="contact-copy"><span className="contact-top"><strong>{contactName(row)}</strong><time dateTime={row.updated_at}>{time(row.updated_at)}</time></span>
      <span className="contact-preview">{row.preview || '暂无文字消息'}</span>
      <span className="contact-bottom"><span className={`contact-status ${row.status}`}>{row.status === 'waiting_human' ? '待接手' : row.status === 'human_active' ? '接待中' : statusLabel[row.status]}</span>{row.needs_reply && row.status === 'human_active' && <span className="needs-reply-badge">待回复</span>}<span className="channel-label">{row.channel === 'qq' ? 'QQ' : '网页'}</span></span>
    </span>{row.unread && <span className="mobile-unread" aria-label="未读消息" />}
  </button>;
}

export default function MobileWorkspace({ queue, csrf, error, syncedAt, qqStatus, page, setPage, chatOpen, open, back, chat, handoff, endAction, logout, busy }: {
  queue: Queue; csrf: string; error: string; syncedAt: string; qqStatus: QqChannelStatus | null; page: Page; setPage: (page: Page) => void;
  chatOpen: boolean; open: (id: string) => void; back: () => void; chat: ReactNode; handoff: ReactNode; endAction: ReactNode; logout: () => void; busy: boolean;
}) {
  const [filter, setFilter] = useState<'all' | 'unread' | 'reply' | 'waiting' | 'active' | 'ended'>('all');
  const [search, setSearch] = useState('');
  const sheet = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (!chatOpen) sheet.current?.close(); }, [chatOpen]);
  const all = [...queue.pending, ...queue.ended].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  const waiting = queue.pending.filter(row => row.status === 'waiting_human');
  const active = queue.pending.filter(row => row.status === 'human_active');
  const unread = all.filter(row => row.unread);
  const replies = all.filter(row => row.needs_reply);
  const rows = (filter === 'reply' ? replies : filter === 'waiting' ? waiting : filter === 'active' ? active : filter === 'ended' ? queue.ended : filter === 'unread' ? unread : all)
    .filter(row => `${contactName(row)} ${row.preview}`.toLowerCase().includes(search.trim().toLowerCase()));
  function inbox(nextFilter: typeof filter) { setFilter(nextFilter); setSearch(''); setPage('messages'); }
  return <div className={`mobile-app ${chatOpen ? 'chat-open' : ''}`}>
    {chatOpen ? <>
      <div className="mobile-chat-toolbar"><button className="icon-button" aria-label="返回会话列表" onClick={back}><MobileIcon name="back" /></button><span>会话</span>{endAction}<button className="text-button" onClick={() => sheet.current?.showModal()}>交接详情</button></div>
      <section className="mobile-chat-content">{chat}</section>
      <dialog className="mobile-handoff-sheet" ref={sheet} aria-labelledby="mobile-handoff-title"><div className="sheet-title"><h2 id="mobile-handoff-title">交接详情</h2><button className="secondary" onClick={() => sheet.current?.close()}>关闭</button></div><div className="sheet-body" role="region" aria-label="交接详情正文" tabIndex={0}>{handoff}</div></dialog>
    </> : <>
      <header className="mobile-page-header"><div><p className="mobile-brand">花窗伞 · 商家</p><h1>{page === 'messages' ? '消息' : page === 'work' ? '工作台' : '我的'}</h1></div><span className={`mobile-sync ${error ? 'interrupted' : ''}`} role="status"><i />{error ? '同步中断' : syncedAt ? '已同步' : '同步中'}</span></header>
      {error && <div className="notice" role="alert">{error}</div>}
      {page === 'messages' && <>
        <label className="mobile-search"><MobileIcon name="search" /><input aria-label="搜索会话" placeholder="搜索会话或消息" value={search} onChange={e => setSearch(e.target.value)} /><span>{search && <button aria-label="清空搜索" onClick={() => setSearch('')}>×</button>}</span></label>
        <div className="mobile-filters" aria-label="会话筛选">{(['all', 'unread', 'reply', 'waiting', 'active', 'ended'] as const).map(value => <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{({ all: '全部', unread: '未读', reply: '待回复', waiting: '待接手', active: '接待中', ended: '已结束' })[value]}</button>)}</div>
        <main className="mobile-inbox">{rows.map(row => <Contact key={row.id} row={row} open={open} />)}{!rows.length && <div className="mobile-empty"><MobileIcon name="message" /><h2>{search ? '没有找到相关会话' : !syncedAt && !error ? '正在同步会话…' : '这里暂时没有会话'}</h2><p>{search ? '试试会话编号或消息中的关键词。' : '客户转人工后，会出现在这里。'}</p></div>}</main>
      </>}
      {page === 'work' && <main className="mobile-work">
        <div className="mobile-service-card"><span className="service-emblem">Q</span><div><strong>QQ 与网页接待</strong><p>在这里接手、回复和结束会话</p></div></div>
        <QqStatusPanel status={qqStatus} syncError={!!error} />
        <div className="mobile-stats"><button onClick={() => inbox('waiting')}><span className="stat-icon"><MobileIcon name="clock" /></span><span>待接手<strong>{waiting.length}</strong></span><MobileIcon name="arrow" /></button><button onClick={() => inbox('active')}><span className="stat-icon wine"><MobileIcon name="message" /></span><span>接待中<strong>{active.length}</strong></span><MobileIcon name="arrow" /></button></div>
        <button className="reply-followup" onClick={() => inbox('reply')}><span>待回复 <strong>{replies.length}</strong></span><span>查看需要跟进的会话 →</span></button><div className="mobile-section-heading"><h2>需要你处理</h2><span>{waiting.length} 待接手 · {active.length} 接待中</span></div>
        <div className="mobile-task-list">{queue.pending.slice().sort((a, b) => Number(b.status === 'waiting_human') - Number(a.status === 'waiting_human') || Number(!!b.needs_reply) - Number(!!a.needs_reply) || Date.parse(b.updated_at) - Date.parse(a.updated_at)).slice(0, 5).map(row => <Contact key={row.id} row={row} open={open} />)}{!queue.pending.length && <p className="mobile-card-empty">暂时没有待处理事项</p>}</div>
        <div className="mobile-section-heading"><h2>快捷操作</h2></div><div className="mobile-shortcuts"><ShipmentImport csrf={csrf} /><button className="secondary" onClick={() => inbox('ended')}><MobileIcon name="file" />已结束会话</button></div>
      </main>}
      {page === 'me' && <main className="mobile-profile"><div className="mobile-profile-card"><span className="profile-avatar"><MobileIcon name="user" /></span><h2>商家接待账号</h2><p>花窗伞 · 每一次咨询，认真接住。</p></div><section className="mobile-settings"><h2>接待说明</h2><p>接手会话后可发送文字回复。结束处理后保留历史记录。</p><p>App 在前台时自动同步；后台运行和系统通知暂未支持。</p><div className="mobile-last-sync">最近同步 <span>{syncedAt ? time(syncedAt) : '尚未同步'}</span></div></section><button className="mobile-logout" onClick={logout} disabled={busy}>{busy ? '正在退出…' : '退出登录'}</button></main>}
      <nav className="mobile-bottom-nav" aria-label="主导航">{([{ id: 'messages', title: '消息', icon: 'message' }, { id: 'work', title: '工作台', icon: 'grid' }, { id: 'me', title: '我的', icon: 'user' }] as const).map(item => <button key={item.id} aria-current={page === item.id ? 'page' : undefined} onClick={() => setPage(item.id)}><span className="nav-icon"><MobileIcon name={item.icon} />{item.id === 'messages' && unread.length > 0 && <i />}</span><span>{item.title}</span></button>)}</nav>
    </>}
  </div>;
}
