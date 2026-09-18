import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Pagination, Segmented, Space } from 'antd'
import { api, errorMessage } from '@/api/client'
import type { Build, Page, Project } from '@/api/types'
import { BuildTable } from '@/components/BuildTable'
import { EmptyState } from '@/components/common'

export function useManualBuild(group: string, project: string) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { message } = App.useApp()
  return useMutation({
    mutationFn: (ref?: string) => api.post<Build>(`projects/${group}/${project}/builds/manual`, { ref }),
    onSuccess: (b) => {
      message.success(`Đã xếp hàng build #${b.number}`)
      qc.invalidateQueries({ queryKey: ['builds'] })
      navigate(`/builds/${b.id}`)
    },
    onError: (e) => message.error(errorMessage(e)),
  })
}

export default function BuildsTab({ project: p }: { project: Project }) {
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<string>('ALL')
  const q = useQuery({
    queryKey: ['builds', p.full_path, page, status],
    queryFn: () =>
      api.get<Page<Build>>(`projects/${p.full_path}/builds`, { page, page_size: 20, status: status === 'ALL' ? undefined : status }),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.items.some((b) => b.status === 'QUEUED' || b.status === 'RUNNING') ? 3000 : 20000,
  })

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={12}>
      <Segmented
        value={status}
        onChange={(v) => {
          setStatus(String(v))
          setPage(1)
        }}
        options={[
          { value: 'ALL', label: 'Tất cả' },
          { value: 'RUNNING', label: 'Đang chạy' },
          { value: 'SUCCESS', label: 'Thành công' },
          { value: 'FAILED', label: 'Thất bại' },
        ]}
      />
      {q.error && <Alert type="error" message={errorMessage(q.error)} />}
      {q.data && q.data.items.length === 0 ? (
        <EmptyState title="Chưa có build nào — push code để kích hoạt build tự động" />
      ) : (
        <BuildTable builds={q.data?.items ?? []} showProject={false} />
      )}
      {q.data && q.data.total > q.data.page_size && (
        <Pagination current={page} pageSize={q.data.page_size} total={q.data.total} onChange={setPage} showSizeChanger={false} />
      )}
    </Space>
  )
}
