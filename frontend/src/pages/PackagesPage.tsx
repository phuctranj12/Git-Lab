import { useState } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Alert, Card, Input, Segmented, Space, Table, Tag, Typography } from 'antd'
import { api, errorMessage } from '@/api/client'
import type { PackageSummary } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { CopyCommand, PageHeader, TimeAgo } from '@/components/common'

export default function PackagesPage() {
  const { meta } = useAuth()
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('popular')
  const pkgs = useQuery({
    queryKey: ['packages', q, sort],
    queryFn: () => api.get<PackageSummary[]>('packages', { q, sort, limit: 200 }),
    placeholderData: keepPreviousData,
  })
  return (
    <>
      <PageHeader title="Package nội bộ" subtitle={`Mọi package ${meta?.internal_package_prefix ?? 'hawee-'}* đã publish trên registry`} />
      {meta && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Typography.Text>Index duy nhất cho pip (package nội bộ + proxy/cache PyPI):</Typography.Text>
            <CopyCommand value={meta.package_index_url} />
          </Space>
        </Card>
      )}
      <Space wrap style={{ marginBottom: 12 }}>
        <Input.Search allowClear placeholder="Tìm package…" onSearch={setQ} style={{ width: 300, maxWidth: '100%' }} />
        <Segmented
          value={sort}
          onChange={(v) => setSort(String(v))}
          options={[
            { value: 'popular', label: 'Phổ biến' },
            { value: 'recent', label: 'Mới publish' },
            { value: 'name', label: 'Tên' },
          ]}
        />
      </Space>
      {pkgs.error && <Alert type="error" message={errorMessage(pkgs.error)} />}
      <Table<PackageSummary>
        rowKey="normalized_name"
        loading={pkgs.isLoading}
        dataSource={pkgs.data}
        pagination={{ pageSize: 25, hideOnSinglePage: true }}
        scroll={{ x: 800 }}
        columns={[
          {
            title: 'Package',
            render: (_, p) => (
              <Space direction="vertical" size={0}>
                <Link to={`/p/${p.project_path}/versions`}>
                  <b>{p.package_name}</b>
                </Link>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {p.description ?? p.project_name}
                </Typography.Text>
              </Space>
            ),
          },
          { title: 'Latest', render: (_, p) => (p.latest_version ? <Tag color="green">{p.latest_version}</Tag> : '—') },
          { title: 'Versions', dataIndex: 'version_count', width: 90 },
          { title: 'Lượt tải', dataIndex: 'download_count', width: 90 },
          { title: 'Publish gần nhất', render: (_, p) => <TimeAgo value={p.last_published_at} />, width: 150 },
          {
            title: 'Cài đặt',
            width: 320,
            render: (_, p) => <CopyCommand value={`pip install ${p.normalized_name}${p.latest_version ? `==${p.latest_version}` : ''}`} />,
          },
        ]}
      />
    </>
  )
}
