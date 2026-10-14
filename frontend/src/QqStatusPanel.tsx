import type { QqChannelStatus } from './api';

export default function QqStatusPanel({ status, syncError }: { status: QqChannelStatus | null; syncError: boolean }) {
  const state = syncError ? 'unavailable' : status?.state || 'unknown';
  const text = { online: '已连接', offline: '已离线', connecting: '连接中', reconnecting: '重连中', unknown: '未上报', unavailable: '状态未更新' }[state];
  const hint = { online: '机器人已连接 QQ，消息结果可在聊天中查看。', offline: '连接已停止或心跳超时，请检查机器人进程。', connecting: '机器人正在建立 QQ 连接。', reconnecting: 'QQ 连接中断，机器人正在重连。', unknown: '尚未收到状态上报，请检查机器人是否已启动。', unavailable: '暂时无法获取最新状态，请检查服务连接。' }[state];
  return <section className={`qq-channel-card ${state}`} aria-label="QQ 通道状态">
    <div><strong>QQ 通道</strong><span className="qq-channel-state"><i />{text}</span></div><p>{hint}</p>
    <dl><dt>最近连接</dt><dd>{status?.last_connected_at ? new Date(status.last_connected_at).toLocaleString('zh-CN', { hour12: false }) : '暂无记录'}</dd><dt>最近上报</dt><dd>{status?.last_heartbeat_at ? new Date(status.last_heartbeat_at).toLocaleString('zh-CN', { hour12: false }) : '暂无记录'}</dd></dl>
  </section>;
}
