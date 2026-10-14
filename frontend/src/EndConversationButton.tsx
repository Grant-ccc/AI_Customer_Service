import { useId, useRef, useState } from 'react';

export default function EndConversationButton({ busy, disabled = false, label = '结束处理', confirm }: {
  busy: boolean; disabled?: boolean; label?: string; confirm: () => Promise<boolean>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [error, setError] = useState('');
  return <>
    <button className="end-conversation-button" disabled={disabled || busy} onClick={() => { setError(''); dialog.current?.showModal(); }}>{label}</button>
    <dialog className="end-confirm-dialog" ref={dialog} aria-labelledby={titleId} onCancel={event => { if (busy) event.preventDefault(); }}>
      <h2 id={titleId}>结束本次人工接待？</h2>
      <p>结束后将停止本次人工接待，聊天记录会保留。客户仍可再次申请人工。</p>
      {error && <p className="end-confirm-error" role="alert">{error}</p>}
      <div className="end-confirm-actions"><button className="secondary" disabled={busy} onClick={() => dialog.current?.close()} autoFocus>继续接待</button><button className="primary" disabled={busy} onClick={async () => {
        setError('');
        if (await confirm()) dialog.current?.close();
        else setError('结束失败，请稍后重试；当前接待状态以页面同步结果为准。');
      }}>{busy ? '正在结束…' : '确认结束'}</button></div>
    </dialog>
  </>;
}
