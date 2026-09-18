import { Navigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Result, Spin } from 'antd'
import { api } from '@/api/client'
import type { Project } from '@/api/types'

export default function PackageRedirect() {
  const { name = '' } = useParams()
  const q = useQuery({ queryKey: ['package', name], queryFn: () => api.get<{ project: Project }>(`packages/${name}`) })
  if (q.isLoading) return <Spin />
  if (q.error || !q.data) return <Result status="404" title="Không tìm thấy package" />
  return <Navigate to={`/p/${q.data.project.full_path}/versions`} replace />
}
