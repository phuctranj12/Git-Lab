import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Breadcrumb, Card, Col, List, Row, Select, Skeleton, Space, Table, Typography } from 'antd'
import { FileOutlined, FolderFilled } from '@ant-design/icons'
import { api, errorMessage } from '@/api/client'
import type { Commit, Project, RefInfo, TreeEntry } from '@/api/types'
import Markdown from '@/components/Markdown'
import { CopyCommand, EmptyState, ShortSha, TimeAgo, formatBytes } from '@/components/common'

interface Refs {
  default_branch: string
  empty: boolean
  branches: RefInfo[]
  tags: RefInfo[]
}

export default function RepositoryTab({ project: p }: { project: Project }) {
  const [ref, setRef] = useState(p.default_branch)
  const [path, setPath] = useState('')
  const [file, setFile] = useState<string | null>(null)
  const base = `projects/${p.full_path}/repository`

  const refs = useQuery({ queryKey: ['refs', p.full_path], queryFn: () => api.get<Refs>(`${base}/refs`) })
  const tree = useQuery({
    queryKey: ['tree', p.full_path, ref, path],
    queryFn: () => api.get<{ entries: TreeEntry[]; empty: boolean }>(`${base}/tree`, { ref, path }),
    enabled: !refs.data?.empty,
  })
  const commits = useQuery({
    queryKey: ['commits', p.full_path, ref],
    queryFn: () => api.get<Commit[]>(`${base}/commits`, { ref, limit: 20 }),
    enabled: !refs.data?.empty,
  })
  const blob = useQuery({
    queryKey: ['blob', p.full_path, ref, file],
    queryFn: () =>
      api.get<{ content: string | null; binary: boolean; truncated: boolean; size: number }>(`${base}/blob`, { ref, path: file! }),
    enabled: !!file,
  })

  if (refs.data?.empty) {
    return (
      <EmptyState title="Repository chưa có commit nào">
        <CopyCommand value={`git clone ${p.clone_url}`} />
      </EmptyState>
    )
  }

  const crumbs = path ? path.split('/') : []
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={16}>
        <Card
          size="small"
          title={
            <Space wrap>
              <Select
                value={ref}
                style={{ minWidth: 180 }}
                onChange={(v) => {
                  setRef(v)
                  setPath('')
                  setFile(null)
                }}
                options={[
                  { label: 'Nhánh', options: (refs.data?.branches ?? []).map((b) => ({ value: b.name, label: b.name })) },
                  { label: 'Tag', options: (refs.data?.tags ?? []).map((t) => ({ value: t.name, label: t.name })) },
                ]}
              />
              <Breadcrumb
                items={[
                  { title: <a onClick={() => { setPath(''); setFile(null) }}>{p.slug}</a> },
                  ...crumbs.map((c, i) => ({
                    title: <a onClick={() => { setPath(crumbs.slice(0, i + 1).join('/')); setFile(null) }}>{c}</a>,
                  })),
                  ...(file ? [{ title: file.split('/').pop() }] : []),
                ]}
              />
            </Space>
          }
          extra={<span className="hide-mobile"><CopyCommand value={`git clone ${p.clone_url}`} /></span>}
        >
          {tree.error && <Alert type="error" message={errorMessage(tree.error)} />}
          {file ? (
            <Skeleton loading={blob.isLoading} active>
              {blob.error && <Alert type="error" message={errorMessage(blob.error)} />}
              {blob.data &&
                (blob.data.binary ? (
                  <Typography.Text type="secondary">File nhị phân ({formatBytes(blob.data.size)}) — không hiển thị.</Typography.Text>
                ) : file.toLowerCase().endsWith('.md') ? (
                  <Markdown source={blob.data.content ?? ''} />
                ) : (
                  <pre className="build-log" style={{ background: '#f6f8fa', color: '#24292f' }}>
                    {blob.data.content}
                    {blob.data.truncated && '\n… (file quá lớn, đã cắt bớt)'}
                  </pre>
                ))}
            </Skeleton>
          ) : (
            <Table<TreeEntry>
              size="small"
              rowKey="path"
              loading={tree.isLoading}
              pagination={false}
              dataSource={[
                ...(path ? [{ name: '..', path: '__up__', type: 'tree' as const, size: null }] : []),
                ...(tree.data?.entries ?? []),
              ]}
              onRow={(e) => ({
                onClick: () => {
                  if (e.path === '__up__') setPath(crumbs.slice(0, -1).join('/'))
                  else if (e.type === 'tree') setPath(e.path)
                  else if (e.type === 'blob') setFile(e.path)
                },
                style: { cursor: 'pointer' },
              })}
              columns={[
                {
                  title: 'Tên',
                  render: (_, e) => (
                    <Space>
                      {e.type === 'tree' ? <FolderFilled style={{ color: '#54aeff' }} /> : <FileOutlined />}
                      {e.name}
                    </Space>
                  ),
                },
                { title: 'Kích thước', width: 120, render: (_, e) => (e.type === 'blob' ? formatBytes(e.size) : '') },
              ]}
            />
          )}
        </Card>
      </Col>
      <Col xs={24} xl={8}>
        <Card size="small" title={`Commit gần đây — ${ref}`}>
          <List
            size="small"
            loading={commits.isLoading}
            dataSource={commits.data ?? []}
            renderItem={(c) => (
              <List.Item>
                <List.Item.Meta
                  title={<Typography.Text ellipsis style={{ maxWidth: 280 }}>{c.subject}</Typography.Text>}
                  description={
                    <Space size={6} wrap style={{ fontSize: 12 }}>
                      <ShortSha sha={c.sha} />
                      <span>{c.author_name}</span>
                      <TimeAgo value={c.authored_at} />
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        </Card>
      </Col>
    </Row>
  )
}
