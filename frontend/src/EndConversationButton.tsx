import { useId, useRef, useState } from 'react';
import type { EndChecks } from './api';

export default function EndConversationButton({ busy, disabled = false, label = '结束处理', checks, prepare, confirm }: {
  busy: boolean; disabled?: boolean; label?: string; checks?: EndChecks; prepare: () => Promise<boolean>; confirm: () => Promise<boolean>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const [ready, setReady] = useState(false);
  const warnings = [];
  if (checks?.needs_reply) warnings.push('本轮还有待回复事项，请确认客户问题是否已处理。');
  if (checks?.qq_pending) warnings.push(`还有 ${checks.qq_pending} 条 QQ 回复等待或正在发送，结束不会取消已排队消息。`);
  if (checks?.qq_failed) warnings.push(`本轮有 ${checks.qq_failed} 条 QQ 回复未成功投递，请核对是否已另行补发。`);
  if (checks?.qq_unknown) warnings.push(`有 ${checks.qq_unknown} 条 QQ 回复结果不明确，请先到 QQ 核对，避免重复回复。`);
  async function checkLatest() {
    setError(''); setChecking(true); setReady(false);
    try { const ok = await prepare(); setReady(ok); if (!ok) setError('无法核对最新会话，暂不能结束，请重试核对。'); }
    finally { setChecking(false); }
  }
  return <>
    <button className="end-conversation-button" disabled={disabled || busy} onClick={() => { dialog.current?.showModal(); void checkLatest(); }}>{label}</button>
    <dialog className="end-confirm-dialog" ref={dialog} aria-labelledby={titleId} onCancel={event => { if (busy) event.preventDefault(); }}>
      <h2 id={titleId}>结束本次人工接待？</h2>
      <p>结束后将停止本次人工接待，聊天记录会保留。客户仍可再次申请人工。</p>
      {checking && <p role="status">正在核对最新消息与投递状态…</p>}
      {ready && warnings.length > 0 && <ul className="end-warnings" aria-label="结束前提醒">{warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>}
      {error && <p className="end-confirm-error" role="alert">{error}</p>}
      {!checking && !ready && <button className="text-button" onClick={() => void checkLatest()}>重新核对</button>}
      <div className="end-confirm-actions"><button className="secondary" disabled={busy} onClick={() => dialog.current?.close()} autoFocus>继续接待</button><button className="primary" disabled={busy || checking || !ready} onClick={async () => {
        setError('');
        if (await confirm()) dialog.current?.close();
        else { setReady(false); setError('结束未成功。请重新核对最新消息与投递状态，再确认结束。'); }
      }}>{busy ? '正在结束…' : '确认结束'}</button></div>
    </dialog>
  </>;
}
