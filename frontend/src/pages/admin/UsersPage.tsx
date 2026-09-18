import { useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Form, Input, Modal, Popconfirm, Space, Switch, Table, Tag, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { api, errorMessage } from '@/api/client'
import type { Page, User } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { PageHeader, TimeAgo } from '@/components/common'

export default function UsersPage() {
  const { message } = App.useApp()
  const { user: me } = useAuth()
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [editing, setEditing] = useState<User | 'new' | null>(null)
  const users = useQuery({
    queryKey: ['users', q, page],
    queryFn: () => api.get<Page<User>>('users', { q, page, page_size: 50 }),
    placeholderData: keepPreviousData,
  })
  const save = useMutation({
    mutationFn: (v: Record<string, unknown>) =>
      editing === 'new' ? api.post<User>('users', v) : api.patch<User>(`users/${(editing as User).id}`, v),
    onSuccess: () => {
      message.success('Đã lưu')
      setEditing(null)
      qc.invalidateQueries({ queryKey: ['users'] })
    },
  })
  const toggle = useMutation({
    mutationFn: (u: User) => api.post<User>(`users/${u.id}/${u.is_active ? 'disable' : 'enable'}`),
    onSuccess: (u) => {
      message.success(u.is_active ? 'Đã kích hoạt' : 'Đã vô hiệu hoá (mọi phiên bị thu hồi)')
      qc.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (e) => message.error(errorMessage(e)),
  })

  const isNew = editing === 'new'
  return (
    <>
      <PageHeader
        title="Người dùng"
        subtitle="Tạo tài khoản, vô hiệu hoá, cấp quyền System Admin"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>
            Tạo user
          </Button>
        }
      />
      <Input.Search
        allowClear
        placeholder="Tìm username, email, tên…"
        onSearch={(v) => {
          setQ(v)
          setPage(1)
        }}
        style={{ width: 320, maxWidth: '100%', marginBottom: 12 }}
      />
      {users.error && <Alert type="error" message={errorMessage(users.error)} />}
      <Table<User>
        rowKey="id"
        loading={users.isLoading}
        dataSource={users.data?.items}
        scroll={{ x: 900 }}
        pagination={{
          current: page,
          pageSize: 50,
          total: users.data?.total,
          onChange: setPage,
          showSizeChanger: false,
          hideOnSinglePage: true,
        }}
        columns={[
          {
            title: 'User',
            render: (_, u) => (
              <Space direction="vertical" size={0}>
                <b>{u.full_name ?? u.username}</b>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  @{u.username} · {u.email}
                </Typography.Text>
              </Space>
            ),
          },
          { title: 'Vai trò', render: (_, u) => (u.is_system_admin ? <Tag color="magenta">System Admin</Tag> : <Tag>User</Tag>) },
          { title: 'Trạng thái', render: (_, u) => (u.is_active ? <Tag color="green">Hoạt động</Tag> : <Tag color="red">Vô hiệu</Tag>) },
          { title: 'Đăng nhập gần nhất', render: (_, u) => <TimeAgo value={u.last_login_at} /> },
          { title: 'Tạo', render: (_, u) => <TimeAgo value={u.created_at} /> },
          {
            title: '',
            width: 200,
            render: (_, u) => (
              <Space>
                <Button size="small" onClick={() => setEditing(u)}>
                  Sửa
                </Button>
                {u.id !== me?.id && (
                  <Popconfirm title={u.is_active ? 'Vô hiệu hoá user? Mọi phiên & quyền truy cập bị chặn ngay.' : 'Kích hoạt lại user?'} onConfirm={() => toggle.mutate(u)}>
                    <Button size="small" danger={u.is_active}>
                      {u.is_active ? 'Vô hiệu hoá' : 'Kích hoạt'}
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Modal title={isNew ? 'Tạo user' : `Sửa ${(editing as User | null)?.username ?? ''}`} open={!!editing} onCancel={() => setEditing(null)} footer={null} destroyOnClose>
        {save.error && <Alert type="error" message={errorMessage(save.error)} style={{ marginBottom: 12 }} />}
        <Form
          layout="vertical"
          initialValues={isNew ? { is_system_admin: false } : { ...(editing as User), password: undefined }}
          onFinish={(v) => {
            const body = { ...v }
            if (!isNew) {
              delete body.username
              if (!body.password) delete body.password
            }
            save.mutate(body)
          }}
        >
          <Form.Item name="username" label="Username" rules={[{ required: isNew }, { pattern: /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/, message: 'Chỉ chữ, số, . _ -' }]}>
            <Input disabled={!isNew} autoComplete="off" />
          </Form.Item>
          <Form.Item name="email" label="Email" rules={[{ required: true, type: 'email' }]}>
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item name="full_name" label="Họ tên">
            <Input />
          </Form.Item>
          <Form.Item
            name="password"
            label={isNew ? 'Mật khẩu ban đầu' : 'Đặt lại mật khẩu (để trống = giữ nguyên)'}
            rules={[{ required: isNew }, { min: 8, message: 'Tối thiểu 8 ký tự' }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="is_system_admin" label="System Admin" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={save.isPending}>
            Lưu
          </Button>
        </Form>
      </Modal>
    </>
  )
}
