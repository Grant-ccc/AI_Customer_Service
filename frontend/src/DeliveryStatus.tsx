import { useState } from 'react';

const states: Record<string, { label: string; help?: string; attention?: boolean }> = {
  pending: { label: '等待发送', help: '已进入发送队列，等待机器人发送。' },
  sending: { label: '发送中', help: '机器人正在向 QQ 平台提交，请勿重复发送。' },
  sent: { label: '平台已接受', help: 'QQ 平台已接受，不代表客户已读。' },
  expired: { label: '窗口过期，未发送', help: '请让客户在 QQ 重新发一条消息，再补充回复。', attention: true },
  quota: { label: '次数用尽，未发送', help: '本条客户消息的回复次数已用完，请让客户重新发消息。', attention: true },
  failed: { label: '发送失败', help: '平台拒绝发送，请检查机器人权限；处理后可复制这条回复再补发。', attention: true },
  unknown: { label: '发送结果待核对', help: '请先到 QQ 核对是否已收到，确认未收到后再补发，避免重复回复。', attention: true },
};

export default function DeliveryStatus({ status, content }: { status?: string | null; content: string }) {
  const info = states[status || ''] || { label: '投递状态待同步', help: '尚未取得明确发送结果，请先核对 QQ 消息。', attention: true };
  const [copied, setCopied] = useState(false);
  const [manualCopy, setManualCopy] = useState(false);
  return <div className={`delivery-status ${info.attention ? 'needs-attention' : ''}`}>
    <span className="delivery-label">QQ · {info.label}</span>
    <span className="delivery-help">{info.help}</span>
    {info.attention && <button type="button" className="delivery-copy" onClick={async () => {
      try { await navigator.clipboard.writeText(content); setCopied(true); setManualCopy(false); }
      catch { setManualCopy(true); }
    }}>{copied ? '已复制回复' : '复制回复'}</button>}
    {manualCopy && <label className="manual-copy">长按下方文字复制<textarea aria-label="待复制的回复" readOnly value={content} onFocus={event => event.currentTarget.select()} /></label>}
  </div>;
}
