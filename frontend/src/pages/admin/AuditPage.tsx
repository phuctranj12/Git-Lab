import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Alert, Button, DatePicker, Input, Select, Space, Table, Tag, Typography } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import type { Dayjs } from 'dayjs'
import { api, buildUrl, errorMessage } from '@/api/client'
import type { AuditEntry, Page } from '@/api/types'
import { PageHeader } from '@/components/common'

const ACTIONS = [
  'LOGIN', 'LOGIN_FAILED', 'CREATE_USER', 'DISABLE_USER', 'CREATE_GROUP', 'ADD_GROUP_MEMBER', 'REMOVE_GROUP_MEMBER',
  'CREATE_PROJECT', 'ARCHIVE_PROJECT', 'DELETE_PROJECT', 'ADD_SSH_KEY', 'REMOVE_SSH_KEY', 'CREATE_TOKEN', 'REVOKE_TOKEN',
  'GIT_PUSH', 'GIT_PUSH_DENIED', 'BUILD_CREATED', 'BUILD_STARTED', 'BUILD_SUCCESS', 'BUILD_FAILED', 'BUILD_CANCELLED',
  'PACKAGE_PUBLISHED', 'PACKAGE_YANKED', 'PACKAGE_DOWNLOADED', 'UPSTREAM_PACKAGE_CACHED', 'AUDIT_EXPORTED',
]

const COLOR: Record<string, string> = {
  LOGIN_FAILED: 'red', GIT_PUSH_DENIED: 'red', BUILD_FAILED: 'red', DISABLE_USER: 'volcano', PACKAGE_YANKED: 'volcano',
  PACKAGE_PUBLISHED: 'green', BUILD_SUCCESS: 'green', GIT_PUSH: 'blue', CREATE_TOKEN: 'purple', REVOKE_TOKEN: 'purple',
}

export default function AuditPage() {
  const [page, setPage] = useState(1)
  const [actions, setActions] = useState<string[]>([])
  const [actor, setActor] = useState('')
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const filters = {
    action: actions.join(',') || undefined,
    q: actor || undefined,
    since: range?.[0]?.startOf('day').toISOString(),
    until: range?.[1]?.endOf('day').toISOString(),
  }
  const logs = useQuery({
    queryKey: ['audit', page, filters],
    queryFn: () => api.get<Page<AuditEntry>>('admin/audit-logs', { ...filters, page, page_size: 50 }),
    placeholderData: keepPreviousData,
  })
  const exportUrl = (format: string) => buildUrl('/api/v1/admin/audit-logs/export', { ...filters, format })

  return (
    <>
      <PageHeader
        title="Audit log"
        subtitle="Append-only — không sửa/xoá được. Lưu tối thiểu 365 ngày."
        extra={
          <>
            <Button icon={<DownloadOutlined />} href={exportUrl('csv')}>
              CSV
            </Button>
            <Button icon={<DownloadOutlined />} href={exportUrl('json')}>
              JSON
            </Button>
          </>
        }
      />
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          mode="multiple"
          allowClear
          placeholder="Lọc action"
          value={actions}
          onChange={(v) => {
            setActions(v)
            setPage(1)
          }}
          options={ACTIONS.map((a) => ({ value: a, label: a }))}
          style={{ minWidth: 280 }}
          maxTagCount="responsive"
        />
        <Input.Search allowClear placeholder="Người thực hiện" onSearch={(v) => { setActor(v); setPage(1) }} style={{ width: 200 }} />
        <DatePicker.RangePicker onChange={(v) => { setRange(v as [Dayjs | null, Dayjs | null] | null); setPage(1) }} />
      </Space>
      {logs.error && <Alert type="error" message={errorMessage(logs.error)} />}
      <Table<AuditEntry>
        rowKey="id"
        size="small"
        loading={logs.isLoading}
        dataSource={logs.data?.items}
        scroll={{ x: 1000 }}
        pagination={{ current: page, pageSize: 50, total: logs.data?.total, onChange: setPage, showSizeChanger: false }}
        expandable={{
          expandedRowRender: (e) => (
            <pre style={{ margin: 0, fontSize: 12 }}>
              {JSON.stringify({ metadata: e.metadata_json, user_agent: e.user_agent, request_id: e.request_id }, null, 2)}
            </pre>
          ),
        }}
        columns={[
          { title: 'Thời gian', width: 170, render: (_, e) => new Date(e.created_at).toLocaleString('vi-VN') },
          { title: 'Action', render: (_, e) => <Tag color={COLOR[e.action]}>{e.action}</Tag> },
          {
            title: 'Người thực hiện',
            render: (_, e) => (
              <Space size={4}>
                <span>{e.actor_name ?? '—'}</span>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {e.actor_type}
                </Typography.Text>
              </Space>
            ),
          },
          { title: 'Đối tượng', render: (_, e) => (e.resource_type ? `${e.resource_type}: ${e.resource_id ?? ''}` : '—') },
          { title: 'IP', dataIndex: 'ip_address', width: 130 },
        ]}
      />
    </>
  )
}
