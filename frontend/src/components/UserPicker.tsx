import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Select } from 'antd'
import { api } from '@/api/client'
import type { UserBrief } from '@/api/types'

export default function UserPicker({ value, onChange }: { value?: string; onChange?: (v: string) => void }) {
  const [q, setQ] = useState('')
  const users = useQuery({
    queryKey: ['user-search', q],
    queryFn: () => api.get<UserBrief[]>('users/search', { q }),
    enabled: q.length >= 1,
  })
  return (
    <Select
      showSearch
      value={value}
      onChange={onChange}
      placeholder="Gõ username / email / tên…"
      filterOption={false}
      onSearch={setQ}
      loading={users.isFetching}
      notFoundContent={q ? 'Không tìm thấy' : 'Nhập để tìm'}
      options={(users.data ?? []).map((u) => ({
        value: u.id,
        label: `${u.full_name ?? u.username} (${u.username} · ${u.email})`,
      }))}
      style={{ width: '100%' }}
    />
  )
}
