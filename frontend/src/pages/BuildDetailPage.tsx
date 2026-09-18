import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Breadcrumb, Button, Card, Col, Descriptions, Popconfirm, Row, Skeleton, Space, Steps, Switch, Table, Tag, Typography } from 'antd'
import { DownloadOutlined, StopOutlined } from '@ant-design/icons'
import { api, ApiError, errorMessage } from '@/api/client'
import type { Artifact, Build } from '@/api/types'
import AnsiLog from '@/components/AnsiLog'
import { BuildStatusTag, ShortSha, TimeAgo, formatBytes, formatDuration } from '@/components/common'

const ACTIVE = new Set(['QUEUED', 'RUNNING'])

function useLiveLog(build: Build | undefined) {
  const [text, setText] = useState('')
  const [denied, setDenied] = useState(false)
  const offset = useRef(0)
  const id = build?.id
  const active = build ? ACTIVE.has(build.status) : false

  useEffect(() => {
    offset.current = 0
    setText('')
    setDenied(false)
  }, [id])

  useEffect(() => {
    if (!id) return
    let stop = false
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => {
      try {
        const r = await api.get<{ content: string; next_offset: number; complete: boolean }>(`builds/${id}/log`, {
          offset: offset.current,
        })
        if (stop) return
        if (r.content) {
          offset.current = r.next_offset
          setText((t) => t + r.content)
        }
        if (!r.complete && active) timer = setTimeout(tick, 2000)
      } catch (e) {
        if (e instanceof ApiError && e.status === 403) setDenied(true)
        else if (!stop && active) timer = setTimeout(tick, 5000)
      }
    }
    tick()
    return () => {
      stop = true
      clearTimeout(timer)
    }
  }, [id, active])
  return { text, denied }
}

export default function BuildDetailPage() {
  const { id = '' } = useParams()
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [follow, setFollow] = useState(true)
  const q = useQuery({
    queryKey: ['build', id],
    queryFn: () => api.get<Build>(`builds/${id}`),
    refetchInterval: (query) => (query.state.data && ACTIVE.has(query.state.data.status) ? 2500 : false),
  })
  const b = q.data
  const log = useLiveLog(b)
  const cancel = useMutation({
    mutationFn: () => api.post<Build>(`builds/${id}/cancel`),
    onSuccess: () => {
      message.success('Đã gửi yêu cầu huỷ')
      qc.invalidateQueries({ queryKey: ['build', id] })
    },
    onError: (e) => message.error(errorMessage(e)),
  })

  if (q.error) return <Alert type="error" message={errorMessage(q.error)} />
  return (
    <Skeleton loading={q.isLoading} active>
      {b && (
        <>
          <Breadcrumb
            style={{ marginBottom: 8 }}
            items={[
              { title: <Link to="/builds">Build</Link> },
              { title: <Link to={`/p/${b.project_path}/builds`}>{b.project_path}</Link> },
              { title: `#${b.number}` },
            ]}
          />
          <div className="page-header">
            <Space wrap>
              <Typography.Title level={3} style={{ margin: 0 }}>
                Build #{b.number}
              </Typography.Title>
              <BuildStatusTag status={b.status} />
              <Tag color={b.publish_package ? 'gold' : 'blue'}>{b.publish_package ? 'RELEASE' : 'VALIDATION'}</Tag>
            </Space>
            {ACTIVE.has(b.status) && (
              <Popconfirm title="Huỷ build này?" onConfirm={() => cancel.mutate()}>
                <Button danger icon={<StopOutlined />} loading={cancel.isPending}>
                  Huỷ build
                </Button>
              </Popconfirm>
            )}
          </div>
          {b.error_code && (
            <Alert
              type={b.status === 'CANCELLED' ? 'warning' : 'error'}
              showIcon
              style={{ marginBottom: 16 }}
              message={<b>{b.error_code}</b>}
              description={b.error_message}
            />
          )}
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={8}>
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Card size="small" title="Thông tin">
                  <Descriptions column={1} size="small" colon={false} labelStyle={{ color: '#8c8c8c', width: 110 }}>
                    <Descriptions.Item label="Project">
                      <Link to={`/p/${b.project_path}`}>{b.project_path}</Link>
                    </Descriptions.Item>
                    <Descriptions.Item label="Trigger">{b.trigger_type}</Descriptions.Item>
                    <Descriptions.Item label="Ref">
                      <Tag>{b.ref_name}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="Commit">
                      <ShortSha sha={b.commit_sha} />
                    </Descriptions.Item>
                    {b.requested_version && <Descriptions.Item label="Version">{b.requested_version}</Descriptions.Item>}
                    <Descriptions.Item label="Runner">{b.runner_name ?? '—'}</Descriptions.Item>
                    <Descriptions.Item label="Người kích hoạt">{b.created_by_username ?? 'git push'}</Descriptions.Item>
                    <Descriptions.Item label="Tạo lúc">
                      <TimeAgo value={b.created_at} />
                    </Descriptions.Item>
                    <Descriptions.Item label="Bắt đầu">
                      <TimeAgo value={b.started_at} />
                    </Descriptions.Item>
                    <Descriptions.Item label="Kết thúc">
                      <TimeAgo value={b.finished_at} />
                    </Descriptions.Item>
                    <Descriptions.Item label="Thời gian">{formatDuration(b.duration_seconds)}</Descriptions.Item>
                  </Descriptions>
                </Card>
                {b.steps.length > 0 && (
                  <Card size="small" title="Các bước">
                    <Steps
                      direction="vertical"
                      size="small"
                      items={b.steps.map((s) => ({
                        title: s.name,
                        status: s.status === 'FAILED' ? 'error' : s.status === 'SKIPPED' ? 'wait' : 'finish',
                        description: s.status === 'SKIPPED' ? 'bỏ qua' : s.duration_seconds != null ? `${s.duration_seconds}s` : undefined,
                      }))}
                    />
                  </Card>
                )}
                {b.artifacts.length > 0 && (
                  <Card size="small" title={b.publish_package ? 'Package đã publish' : 'Artifact'}>
                    <Table<Artifact>
                      size="small"
                      rowKey="filename"
                      pagination={false}
                      dataSource={b.artifacts}
                      columns={[
                        { title: 'File', render: (_, a) => <Typography.Text style={{ fontSize: 12 }}>{a.filename}</Typography.Text> },
                        { title: '', width: 70, render: (_, a) => formatBytes(a.size_bytes) },
                        {
                          title: '',
                          width: 40,
                          render: (_, a) => (
                            <Button size="small" icon={<DownloadOutlined />} href={`/api/v1/builds/${b.id}/artifacts/${a.filename}`} />
                          ),
                        },
                      ]}
                    />
                  </Card>
                )}
              </Space>
            </Col>
            <Col xs={24} xl={16}>
              <Card
                size="small"
                title="Console log"
                extra={
                  <Space>
                    <span style={{ fontSize: 12 }}>Tự cuộn</span>
                    <Switch size="small" checked={follow} onChange={setFollow} />
                  </Space>
                }
              >
                {log.denied ? (
                  <Alert type="info" message="Cần quyền Developer trở lên để xem log build." />
                ) : (
                  <AnsiLog text={log.text || (b.status === 'QUEUED' ? 'Đang chờ runner nhận build…\n' : '')} follow={follow} />
                )}
              </Card>
            </Col>
          </Row>
        </>
      )}
    </Skeleton>
  )
}
