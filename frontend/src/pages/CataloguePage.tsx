import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Input, Pagination, Row, Select, Skeleton, Space, Tag, Typography } from 'antd'
import { BranchesOutlined, BuildOutlined, HistoryOutlined, PlusOutlined } from '@ant-design/icons'
import { api, errorMessage } from '@/api/client'
import type { Page, Project } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import CreateProjectModal from '@/components/CreateProjectModal'
import { BuildStatusTag, CopyCommand, EmptyState, PageHeader, VisibilityTag } from '@/components/common'

export function ToolCard({ p }: { p: Project }) {
  return (
    <Card className="tool-card" size="small">
      <Space align="start" style={{ justifyContent: 'space-between', width: '100%' }}>
        <div style={{ minWidth: 0 }}>
          <Link to={`/p/${p.full_path}`}>
            <Typography.Title level={5} style={{ margin: 0 }} ellipsis>
              {p.name}
            </Typography.Title>
          </Link>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {p.full_path}
          </Typography.Text>
        </div>
        <Space size={4}>
          {p.archived && <Tag>Archived</Tag>}
          <VisibilityTag value={p.visibility} />
        </Space>
      </Space>
      <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ margin: 0, minHeight: 44 }}>
        {p.description || 'Chưa có mô tả'}
      </Typography.Paragraph>
      <dl className="kv" style={{ margin: 0 }}>
        <dt>Package</dt>
        <dd>{p.package_name ? <code>{p.package_name}</code> : '—'}</dd>
        <dt>Latest</dt>
        <dd>{p.latest_release_version ? <Tag color="green">{p.latest_release_version}</Tag> : 'Chưa release'}</dd>
        <dt>Python</dt>
        <dd>{p.python_requires ?? '—'}</dd>
        <dt>Build</dt>
        <dd>
          <BuildStatusTag status={p.last_build_status} short />
        </dd>
        <dt>Owner</dt>
        <dd>{p.group_name}</dd>
      </dl>
      {p.install_command && p.latest_release_version && <CopyCommand value={p.install_command} />}
      <Space size={4} wrap style={{ marginTop: 'auto' }}>
        <Link to={`/p/${p.full_path}/repository`}>
          <Button size="small" icon={<BranchesOutlined />}>Source</Button>
        </Link>
        <Link to={`/p/${p.full_path}/versions`}>
          <Button size="small" icon={<HistoryOutlined />}>Versions</Button>
        </Link>
        <Link to={`/p/${p.full_path}/builds`}>
          <Button size="small" icon={<BuildOutlined />}>Builds</Button>
        </Link>
      </Space>
    </Card>
  )
}

export default function CataloguePage() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const [creating, setCreating] = useState(false)
  const q = params.get('q') ?? ''
  const page = Number(params.get('page') ?? 1)
  const scope = params.get('scope') ?? 'all'
  const sort = params.get('sort') ?? 'updated'
  const archived = params.get('archived') === '1'

  const projects = useQuery({
    queryKey: ['projects', { q, page, scope, sort, archived }],
    queryFn: () =>
      api.get<Page<Project>>('projects', { q, page, page_size: 24, mine: scope === 'mine', sort, include_archived: archived }),
    placeholderData: keepPreviousData,
  })

  const update = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)))
    if (!('page' in patch)) next.delete('page')
    setParams(next, { replace: true })
  }

  return (
    <>
      <PageHeader
        title="Danh mục tool"
        subtitle="Tìm theo tên, package, mô tả hoặc group"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
            Tạo tool mới
          </Button>
        }
      />
      <Space wrap style={{ marginBottom: 16 }}>
        <Input.Search
          allowClear
          defaultValue={q}
          placeholder="PDF Parser, hawee-…, bóc tách…"
          onSearch={(v) => update({ q: v || null })}
          style={{ width: 320, maxWidth: '100%' }}
        />
        <Select
          value={scope}
          onChange={(v) => update({ scope: v === 'all' ? null : v })}
          options={[
            { value: 'all', label: 'Tất cả tool' },
            { value: 'mine', label: 'Tool của tôi' },
          ]}
          style={{ width: 150 }}
        />
        <Select
          value={sort}
          onChange={(v) => update({ sort: v === 'updated' ? null : v })}
          options={[
            { value: 'updated', label: 'Mới cập nhật' },
            { value: 'name', label: 'Tên A→Z' },
            { value: 'created', label: 'Mới tạo' },
          ]}
          style={{ width: 150 }}
        />
        <Select
          value={archived ? '1' : '0'}
          onChange={(v) => update({ archived: v === '1' ? '1' : null })}
          options={[
            { value: '0', label: 'Ẩn archived' },
            { value: '1', label: 'Gồm archived' },
          ]}
          style={{ width: 140 }}
        />
      </Space>
      {projects.error && <Alert type="error" message={errorMessage(projects.error)} />}
      <Skeleton loading={projects.isLoading} active>
        {projects.data && projects.data.items.length === 0 ? (
          <EmptyState title={q ? `Không tìm thấy tool khớp "${q}"` : 'Chưa có tool nào'}>
            {user && (
              <Button type="primary" onClick={() => setCreating(true)}>
                Tạo tool đầu tiên
              </Button>
            )}
          </EmptyState>
        ) : (
          <Row gutter={[16, 16]}>
            {projects.data?.items.map((p) => (
              <Col key={p.id} xs={24} sm={12} xl={8} xxl={6}>
                <ToolCard p={p} />
              </Col>
            ))}
          </Row>
        )}
        {projects.data && projects.data.total > projects.data.page_size && (
          <Pagination
            style={{ marginTop: 20, textAlign: 'center' }}
            current={page}
            pageSize={projects.data.page_size}
            total={projects.data.total}
            onChange={(p) => update({ page: String(p) })}
            showSizeChanger={false}
          />
        )}
      </Skeleton>
      <CreateProjectModal open={creating} onClose={() => setCreating(false)} />
    </>
  )
}
