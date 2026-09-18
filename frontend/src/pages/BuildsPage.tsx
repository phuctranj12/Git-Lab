import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Alert, Pagination, Segmented, Space } from 'antd'
import { api, errorMessage } from '@/api/client'
import type { Build, Page } from '@/api/types'
import { BuildTable } from '@/components/BuildTable'
import { EmptyState, PageHeader } from '@/components/common'

export default function BuildsPage() {
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('ALL')
  const q = useQuery({
    queryKey: ['builds', 'all', page, status],
    queryFn: () => api.get<Page<Build>>('builds', { page, page_size: 25, status: status === 'ALL' ? undefined : status }),
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
  })
  return (
    <>
      <PageHeader title="Build" subtitle="Build trên mọi project bạn được xem — tự cập nhật" />
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Segmented
          value={status}
          onChange={(v) => {
            setStatus(String(v))
            setPage(1)
          }}
          options={[
            { value: 'ALL', label: 'Tất cả' },
            { value: 'QUEUED', label: 'Đang chờ' },
            { value: 'RUNNING', label: 'Đang chạy' },
            { value: 'SUCCESS', label: 'Thành công' },
            { value: 'FAILED', label: 'Thất bại' },
          ]}
        />
        {q.error && <Alert type="error" message={errorMessage(q.error)} />}
        {q.data?.items.length === 0 ? <EmptyState title="Không có build" /> : <BuildTable builds={q.data?.items ?? []} />}
        {q.data && q.data.total > q.data.page_size && (
          <Pagination current={page} pageSize={q.data.page_size} total={q.data.total} onChange={setPage} showSizeChanger={false} />
        )}
      </Space>
    </>
  )
}
