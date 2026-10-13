import { useEffect, useState } from 'react';
import { api, ApiError } from './api';

type Preview = { import_id: string; total: number; new: number; updated: number; sample: { tracking_number: string; phone: string }[] };
type Status = { count: number; updated_at: string | null };

export default function ShipmentImport({ csrf }: { csrf: string }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [filename, setFilename] = useState('');
  useEffect(() => { if (open) api<Status>('/merchant/shipments').then(setStatus).catch(e => setError(e.message)); }, [open]);
  async function upload(file: File) {
    setBusy(true); setPreview(null); setError(''); setMessage(''); setFilename(file.name);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 30000);
    try {
      if (!file.name.toLowerCase().endsWith('.xlsx')) throw new Error('请选择xlsx文件。');
      if (file.size > 5 * 1024 * 1024) throw new Error('文件不能超过5MB。');
      const response = await fetch('/api/merchant/shipments/preview', {
        method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': csrf }, body: file,
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = data.detail;
        throw new ApiError(response.status, typeof detail === 'string' ? detail :
          (detail.message || '上传失败') + '\n' + (detail.issues || []).map((x: { sheet: string; row: number; reason: string }) => x.sheet + ' 第' + x.row + '行：' + x.reason).join('\n'));
      }
      setPreview(data);
    } catch (e) { setError(e instanceof Error ? e.message : '上传失败，请重试。'); }
    finally { window.clearTimeout(timer); setBusy(false); }
  }
  async function confirm() {
    if (!preview || busy) return;
    setBusy(true); setError('');
    try {
      await api('/merchant/shipments/confirm', { import_id: preview.import_id }, csrf);
      setMessage('已导入 ' + preview.total + ' 条记录。用户可以在私聊发送收件手机号查询。');
      setPreview(null);
      setStatus(await api<Status>('/merchant/shipments'));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="shipment-tools">
    <button className="secondary" onClick={() => setOpen(true)}>发货表管理</button>
    {open && <div className="shipment-backdrop"><section className="shipment-dialog" role="dialog" aria-modal="true" aria-labelledby="shipment-title">
      <div className="shipment-title"><h2 id="shipment-title">发货表管理</h2><button className="text-button" disabled={busy} onClick={() => setOpen(false)}>关闭</button></div>
      <p className="muted">按快递单号新增或更新。未出现在新表中的旧记录继续保留。</p>
      {status && <p className="shipment-count">现有 {status.count} 条记录{status.updated_at ? ' · 最近更新 ' + new Date(status.updated_at).toLocaleString('zh-CN', { hour12: false }) : ''}</p>}
      <label className="shipment-file">上传发货单（xlsx，最多5MB）<input type="file" accept=".xlsx" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file) void upload(file); e.target.value = ''; }} /></label>
      <p className="muted">读取「快递单号」和「手机／手机号／固话」。只保存手机号、单号和更新时间。</p>
      {busy && <p role="status">正在处理…</p>}
      {error && <div className="notice shipment-error" role="alert">{error}</div>}
      {message && <p className="shipment-success" role="status">{message}</p>}
      {preview && <div className="shipment-preview">
        <h3>确认导入 {filename}</h3><p>共 {preview.total} 条：新增 {preview.new} 条，更新 {preview.updated} 条。</p>
        <table><thead><tr><th>快递单号</th><th>手机号</th></tr></thead><tbody>{preview.sample.map(r => <tr key={r.tracking_number}><td>{r.tracking_number}</td><td>{r.phone}</td></tr>)}</tbody></table>
        <p className="muted">以上仅展示前5条。确认前不会修改发货记录。</p>
        <button className="primary" disabled={busy} onClick={() => void confirm()}>确认导入</button>
      </div>}
    </section></div>}
  </div>;
}
