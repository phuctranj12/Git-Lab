import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Form, Modal, Popconfirm, Select, Table, Tag, Typography } from 'antd'
import { UserAddOutlined } from '@ant-design/icons'
import { api, errorMessage } from '@/api/client'
import type { Project, ProjectMember } from '@/api/types'
import { RoleTag, TimeAgo } from '@/components/common'
import UserPicker from '@/components/UserPicker'

export default function MembersTab({ project: p }: { project: Project }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const canManage = p.permissions.includes('project.members')
  const key = ['project-members', p.full_path]
  const members = useQuery({ queryKey: key, queryFn: () => api.get<ProjectMember[]>(`projects/${p.full_path}/members`) })
  const add = useMutation({
    mutationFn: (v: { user_id: string; role: string }) => api.post(`projects/${p.full_path}/members`, v),
    onSuccess: () => {
      setOpen(false)
      message.success('Đã cấp quyền')
      qc.invalidateQueries({ queryKey: key })
    },
  })
  const remove = useMutation({
    mutationFn: (userId: string) => api.del(`projects/${p.full_path}/members/${userId}`),
    onSuccess: () => {
      message.success('Đã gỡ quyền riêng')
      qc.invalidateQueries({ queryKey: key })
    },
    onError: (e) => message.error(errorMessage(e)),
  })

  return (
    <>
      <div className="page-header" style={{ marginBottom: 12 }}>
        <Typography.Text type="secondary">
          Quyền mặc định lấy từ group <Link to={`/groups/${p.group_slug}`}>{p.group_name}</Link>. Có thể cấp thêm quyền riêng
          cho project này.
        </Typography.Text>
        {canManage && (
          <Button icon={<UserAddOutlined />} onClick={() => setOpen(true)}>
            Cấp quyền
          </Button>
        )}
      </div>
      {members.error && <Alert type="error" message={errorMessage(members.error)} />}
      <Table<ProjectMember>
        rowKey={(m) => m.user.id}
        loading={members.isLoading}
        dataSource={members.data}
        pagination={false}
        scroll={{ x: 600 }}
        columns={[
          { title: 'Người dùng', render: (_, m) => <span><b>{m.user.full_name ?? m.user.username}</b> <Typography.Text type="secondary">@{m.user.username}</Typography.Text></span> },
          { title: 'Role', render: (_, m) => <RoleTag role={m.role} /> },
          { title: 'Nguồn', render: (_, m) => (m.source === 'group' ? <Tag>group</Tag> : <Tag color="blue">project</Tag>) },
          { title: 'Từ', render: (_, m) => <TimeAgo value={m.created_at} /> },
          {
            title: '',
            width: 100,
            render: (_, m) =>
              canManage && m.source === 'project' ? (
                <Popconfirm title="Gỡ quyền riêng trên project?" onConfirm={() => remove.mutate(m.user.id)}>
                  <Button size="small" danger>
                    Gỡ
                  </Button>
                </Popconfirm>
              ) : null,
          },
        ]}
      />
      <Modal title="Cấp quyền trên project" open={open} onCancel={() => setOpen(false)} footer={null} destroyOnClose>
        {add.error && <Alert type="error" message={errorMessage(add.error)} style={{ marginBottom: 12 }} />}
        <Form layout="vertical" initialValues={{ role: 'DEVELOPER' }} onFinish={(v) => add.mutate(v)}>
          <Form.Item name="user_id" label="Người dùng" rules={[{ required: true }]}>
            <UserPicker />
          </Form.Item>
          <Form.Item name="role" label="Role">
            <Select
              options={[
                { value: 'VIEWER', label: 'Viewer — xem, clone, cài package' },
                { value: 'DEVELOPER', label: 'Developer — push nhánh, chạy build, xem log' },
                { value: 'MAINTAINER', label: 'Maintainer — tag/release, yank, token, cài đặt' },
              ]}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={add.isPending}>
            Cấp quyền
          </Button>
        </Form>
      </Modal>
    </>
  )
}
