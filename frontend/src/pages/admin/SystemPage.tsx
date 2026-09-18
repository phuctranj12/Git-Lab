import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Badge, Card, Col, Descriptions, Row, Select, Skeleton, Space, Statistic, Table, Tag } from 'antd'
import { api, errorMessage } from '@/api/client'
import type { Runner } from '@/api/types'
import { PageHeader, TimeAgo, formatBytes } from '@/components/common'

interface SystemStatus {
  app: { name: string; env: string; version: string }
  readiness: { ready: boolean; checks: { name: string; ok: boolean; error?: string }[] }
  queue_depth: number | null
  counts: Record<string, number>
  storage: { internal_package_bytes: number; upstream_cache_bytes: number; upstream_cached_files: number }
  settings: Record<string, string | number | boolean>
}

export default function SystemPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const sys = useQuery({ queryKey: ['system'], queryFn: () => api.get<SystemStatus>('admin/system'), refetchInterval: 10000 })
  const runners = useQuery({ queryKey: ['runners'], queryFn: () => api.get<Runner[]>('admin/runners'), refetchInterval: 10000 })
  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => api.patch(`admin/runners/${id}`, { status }),
    onSuccess: () => {
      message.success('Đã cập nhật runner')
      qc.invalidateQueries({ queryKey: ['runners'] })
    },
    onError: (e) => message.error(errorMessage(e)),
  })
  const s = sys.data

  return (
    <>
      <PageHeader title="Hệ thống" subtitle="Sức khoẻ dịch vụ, hàng đợi build, runner, dung lượng" />
      {sys.error && <Alert type="error" message={errorMessage(sys.error)} />}
      <Skeleton loading={sys.isLoading} active>
        {s && (
          <Row gutter={[16, 16]}>
            <Col xs={24} lg={8}>
              <Card
                title="Readiness"
                extra={s.readiness.ready ? <Tag color="green">READY</Tag> : <Tag color="red">NOT READY</Tag>}
              >
                <Space direction="vertical" style={{ width: '100%' }}>
                  {s.readiness.checks.map((c) => (
                    <div key={c.name}>
                      <Badge status={c.ok ? 'success' : 'error'} text={<b>{c.name}</b>} />
                      {c.error && <div style={{ color: '#cf1322', fontSize: 12 }}>{c.error}</div>}
                    </div>
                  ))}
                  <div style={{ fontSize: 12, color: '#8c8c8c' }}>
                    {s.app.name} v{s.app.version} · env {s.app.env}
                  </div>
                </Space>
              </Card>
            </Col>
            <Col xs={24} lg={16}>
              <Row gutter={[16, 16]}>
                {[
                  ['User hoạt động', s.counts.active_users],
                  ['Project', s.counts.projects],
                  ['Build đang chạy', s.counts.builds_running],
                  ['Build chờ (DB)', s.counts.builds_queued],
                  ['Hàng đợi Redis', s.queue_depth ?? '—'],
                  ['File PyPI đã cache', s.storage.upstream_cached_files],
                ].map(([label, value]) => (
                  <Col key={String(label)} xs={12} md={8}>
                    <Card size="small" className="stat-card">
                      <Statistic title={label} value={value as number} />
                    </Card>
                  </Col>
                ))}
              </Row>
            </Col>
            <Col xs={24} lg={12}>
              <Card title="Dung lượng">
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="Package nội bộ">{formatBytes(s.storage.internal_package_bytes)}</Descriptions.Item>
                  <Descriptions.Item label="Cache PyPI">{formatBytes(s.storage.upstream_cache_bytes)}</Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card title="Cấu hình (chỉ đọc — đổi qua .env)">
                <Descriptions column={1} size="small">
                  {Object.entries(s.settings).map(([k, v]) => (
                    <Descriptions.Item key={k} label={k}>
                      <code>{String(v)}</code>
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              </Card>
            </Col>
          </Row>
        )}
      </Skeleton>
      <Card title="Build runner" style={{ marginTop: 16 }}>
        <Table<Runner>
          rowKey="id"
          size="small"
          loading={runners.isLoading}
          dataSource={runners.data}
          pagination={false}
          scroll={{ x: 800 }}
          locale={{ emptyText: 'Chưa có runner nào gửi heartbeat' }}
          columns={[
            { title: 'Runner', render: (_, r) => <Badge status={r.online ? 'success' : 'default'} text={<b>{r.name}</b>} /> },
            { title: 'Host', dataIndex: 'hostname' },
            { title: 'Job', render: (_, r) => `${r.current_jobs} / ${r.max_concurrent_jobs}` },
            { title: 'Heartbeat', render: (_, r) => <TimeAgo value={r.last_heartbeat_at} /> },
            { title: 'Phiên bản', dataIndex: 'version' },
            {
              title: 'Trạng thái',
              width: 160,
              render: (_, r) => (
                <Select
                  size="small"
                  value={r.status}
                  style={{ width: 140 }}
                  onChange={(status) => setStatus.mutate({ id: r.id, status })}
                  options={[
                    { value: 'ONLINE', label: 'ONLINE' },
                    { value: 'DRAINING', label: 'DRAINING' },
                    { value: 'OFFLINE', label: 'OFFLINE' },
                  ]}
                />
              ),
            },
          ]}
        />
      </Card>
    </>
  )
}
