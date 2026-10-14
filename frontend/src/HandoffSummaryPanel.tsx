import type { Conversation, Summary, SummaryFact } from './api';

const sections: [keyof Summary, string][] = [['request', '用户诉求'], ['known', '已知情况'], ['attempted', '已尝试操作'], ['materials', '材料情况']];
const statusText: Record<string, string> = { queued: '摘要等待生成', generating: '正在整理摘要…', failed: '摘要生成失败，请查看完整记录。', limited: '摘要暂时无法生成，请查看完整记录。', not_connected: '本次未启用摘要，请查看完整记录。', stale: '此摘要已失效，请查看最新交接。' };

function Fact({ fact, compact = false }: { fact: SummaryFact; compact?: boolean }) {
  return <div className="summary-fact">
    {compact && fact.text.length > 80 ? <details className="fact-full-text"><summary><span className="fact-preview">{fact.text}</span><span className="fold-hint"><span className="fold-closed">展开全文</span><span className="fold-open">收起全文</span></span></summary></details> : <p>{fact.text}</p>}
    <details className="summary-evidence"><summary>查看依据 · 第 {fact.sequence} 条</summary><blockquote>{fact.quote}</blockquote></details>
  </div>;
}

export default function HandoffSummaryPanel({ conversation, compact = false }: { conversation: Conversation; compact?: boolean }) {
  const h = conversation.handoff!;
  const start = h.summary_start_sequence;
  const end = h.summary_covered_sequence;
  const ready = h.summary_status === 'ready' && h.summary;
  const additional = end !== null && conversation.messages.some(m => m.role === 'user' && m.sequence > end);
  const scope = start !== null && end !== null ? (end >= start ? `仅整理本轮第 ${start}—${end} 条消息。` : '转交前暂无用户消息。') : '';
  const requests = ready ? h.summary!.request : [];
  if (compact) return <section className="handoff-summary compact-summary" aria-label="交接摘要">
    <div className="handoff-brief-heading"><h2>接待重点</h2><span>第 {h.round} 次交接{h.ended_at ? ' · 已结束' : ''}</span></div>
    <section className="request-overview" aria-label="客户诉求"><h3>客户想解决什么</h3>
      {ready ? <>
        <p className="summary-caution">AI 整理，用户陈述尚未核实，请结合聊天记录核对。</p>
        {requests.length ? requests.slice(0, 2).map((fact, i) => <Fact key={i} fact={fact} compact />) : <p className="muted">未提供明确诉求，请查看聊天记录。</p>}
        {requests.length > 2 && <details className="more-requests"><summary>更多诉求（{requests.length - 2} 项）</summary>{requests.slice(2).map((fact, i) => <Fact key={i} fact={fact} compact />)}</details>}
      </> : <p className="summary-unavailable" role="status">{statusText[h.summary_status] || '请查看完整记录。'}</p>}
    </section>
    <details className="handoff-reason"><summary><span className="reason-label">为什么转人工</span><span className="reason-preview">{h.reason || '未记录转交原因'}</span>{h.reason.length > 80 && <span className="fold-hint"><span className="fold-closed">展开完整原因</span><span className="fold-open">收起原因</span></span>}</summary></details>
    {additional && <p className="summary-extra">摘要之后有客户新补充，请先查看聊天记录。</p>}
    {ready && <div className="summary-folds">{sections.filter(([key]) => key !== 'request').map(([key, title]) => <details className="summary-fold" key={key}>
      <summary>{title}<span>{h.summary![key].length ? `${h.summary![key].length} 项` : '未提供'}</span></summary>
      {h.summary![key].length ? h.summary![key].map((fact, i) => <Fact key={i} fact={fact} compact />) : <p className="muted">本轮未提供相关信息。</p>}
    </details>)}</div>}
    <details className="handoff-metadata"><summary>交接时间与记录范围</summary><dl><dt>转交时间</dt><dd>{new Date(h.created_at).toLocaleString('zh-CN', { hour12: false })}</dd><dt>转交时记录范围</dt><dd>{h.covered_sequence ? `第 1—${h.covered_sequence} 条消息` : '转交前暂无消息'}</dd>{scope && <><dt>摘要覆盖范围</dt><dd>{scope}</dd></>}</dl></details>
    <small>摘要不影响接手，可直接查看聊天记录进行接待。</small>
  </section>;
  return <section className="handoff-summary" aria-label="交接摘要"><h3>交接摘要</h3>
    {h.summary_status === 'ready' && h.summary ? <>
      <p className="muted">AI 整理，接待前请核对。用户陈述尚未核实。</p>
      {sections.map(([key, title]) => <div className="summary-section" key={key}><h4>{title}</h4>
        {h.summary![key].length ? h.summary![key].map((fact, i) => <Fact key={i} fact={fact} />) : <p className="muted">未提供</p>}
      </div>)}
    </> : <p role="status">{statusText[h.summary_status] || '请查看完整记录。'}</p>}
    {start !== null && end !== null && <small>{end >= start ? `仅整理本轮第 ${start}—${end} 条消息。` : '转交前暂无用户消息。'}摘要不影响接手。</small>}
    {additional && <p className="summary-extra">摘要之后有客户新补充，请查看完整聊天记录。</p>}
  </section>;
}
