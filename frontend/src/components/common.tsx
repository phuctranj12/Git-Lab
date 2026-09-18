import { useState, type ReactNode } from 'react'
import { Button, Empty, Space, Tag, Tooltip, Typography, message } from 'antd'
import {
  CheckCircleFilled,
  CheckOutlined,
  ClockCircleOutlined,
  CloseCircleFilled,
  CopyOutlined,
  LoadingOutlined,
  StopOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/vi'
import type { BuildStatus, Level } from '@/api/types'

dayjs.extend(relativeTime)
dayjs.locale('vi')

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  }
}

/** Ô lệnh một dòng kèm nút copy (install command, clone URL, token…). */
export function CopyCommand({ value, prefix, secret }: { value: string; prefix?: string; secret?: boolean }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="copy-command">
      {prefix && <span className="copy-prefix">{prefix}</span>}
      <code className={secret ? 'secret' : undefined}>{value}</code>
      <Tooltip title={copied ? 'Đã copy' : 'Copy'}>
        <Button
          size="small"
          type="text"
          icon={copied ? <CheckOutlined style={{ color: '#52c41a' }} /> : <CopyOutlined />}
          onClick={async () => {
            if (await copyToClipboard(value)) {
              setCopied(true)
              setTimeout(() => setCopied(false), 1500)
            } else message.error('Không copy được')
          }}
          aria-label="Copy"
        />
      </Tooltip>
    </div>
  )
}

const STATUS: Record<BuildStatus, { color: string; icon: ReactNode; label: string }> = {
  QUEUED: { color: 'default', icon: <ClockCircleOutlined />, label: 'Đang chờ' },
  RUNNING: { color: 'processing', icon: <LoadingOutlined />, label: 'Đang chạy' },
  SUCCESS: { color: 'success', icon: <CheckCircleFilled />, label: 'Thành công' },
  FAILED: { color: 'error', icon: <CloseCircleFilled />, label: 'Thất bại' },
  CANCELLED: { color: 'warning', icon: <StopOutlined />, label: 'Đã huỷ' },
}

export function BuildStatusTag({ status, short }: { status: BuildStatus | null | undefined; short?: boolean }) {
  if (!status) return <Tag>Chưa build</Tag>
  const s = STATUS[status]
  return (
    <Tag color={s.color} icon={s.icon} style={{ marginInlineEnd: 0 }}>
      {short ? status : s.label}
    </Tag>
  )
}

const LEVEL_COLOR: Record<string, string> = {
  ADMIN: 'magenta',
  OWNER: 'gold',
  MAINTAINER: 'purple',
  DEVELOPER: 'blue',
  VIEWER: 'default',
}

export function RoleTag({ role }: { role: string | Level }) {
  const label: Record<string, string> = {
    ADMIN: 'Admin',
    OWNER: 'Owner',
    MAINTAINER: 'Maintainer',
    DEVELOPER: 'Developer',
    VIEWER: 'Viewer',
    NONE: 'Không có quyền',
  }
  return <Tag color={LEVEL_COLOR[role] ?? 'default'}>{label[role] ?? role}</Tag>
}

export function VisibilityTag({ value }: { value: 'PRIVATE' | 'INTERNAL' }) {
  return value === 'PRIVATE' ? <Tag color="red">Private</Tag> : <Tag color="cyan">Internal</Tag>
}

export function TimeAgo({ value }: { value: string | null | undefined }) {
  if (!value) return <Typography.Text type="secondary">—</Typography.Text>
  return (
    <Tooltip title={dayjs(value).format('DD/MM/YYYY HH:mm:ss')}>
      <span>{dayjs(value).fromNow()}</span>
    </Tooltip>
  )
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m ? `${m}m${String(s).padStart(2, '0')}s` : `${s}s`
}

export function formatBytes(n: number | null | undefined): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function ShortSha({ sha }: { sha: string | null | undefined }) {
  if (!sha) return <Typography.Text type="secondary">—</Typography.Text>
  return (
    <Tooltip title={sha}>
      <Typography.Text code copyable={{ text: sha, tooltips: ['Copy SHA', 'Đã copy'] }}>
        {sha.slice(0, 7)}
      </Typography.Text>
    </Tooltip>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Empty description={title} style={{ padding: '32px 0' }}>
      {children && <Space direction="vertical">{children}</Space>}
    </Empty>
  )
}

export function PageHeader({ title, subtitle, extra }: { title: ReactNode; subtitle?: ReactNode; extra?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
        {subtitle && <Typography.Text type="secondary">{subtitle}</Typography.Text>}
      </div>
      {extra && <Space wrap>{extra}</Space>}
    </div>
  )
}
