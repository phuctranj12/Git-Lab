import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { api, errorMessage } from '@/api/client'
import type { Project, Token, TokenCreated } from '@/api/types'
import { CopyCommand, TimeAgo } from '@/components/common'

export default function ServiceTokens({ project: p }: { project: Project }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [created, setCreated] = useState<TokenCreated | null>(null)
  const key = ['project-tokens', p.full_path]
  const tokens = useQuery({ queryKey: key, queryFn: () => api.get<Token[]>(`projects/${p.full_path}/tokens`) })
  const create = useMutation({
    mutationFn: (body: { name: string; scopes: string[]; expires_in_days?: number }) =>
      api.post<TokenCreated>(`projects/${p.full_path}/tokens`, body),
    onSuccess: (t) => {
      setOpen(false)
      setCreated(t)
      qc.invalidateQueries({ queryKey: key })
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => api.del(`projects/${p.full_path}/tokens/${id}`),
    onSuccess: () => {
      message.success('Đã thu hồi token')
      qc.invalidateQueries({ queryKey: key })
    },
    onError: (e) => message.error(errorMessage(e)),
  })

  return (
    <>
      <Space style={{ marginBottom: 8 }}>
        <Button size="small" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          Tạo service token
        </Button>
      </Space>
      <Table<Token>
        size="small"
        rowKey="id"
        loading={tokens.isLoading}
        dataSource={tokens.data}
        pagination={false}
        locale={{ emptyText: 'Chưa có service token' }}
        columns={[
          { title: 'Tên', dataIndex: 'name' },
          { title: 'Scope', render: (_, t) => t.scopes.map((s) => <Tag key={s}>{s}</Tag>) },
          { title: 'Dùng lần cuối', render: (_, t) => <TimeAgo value={t.last_used_at} /> },
          {
            title: '',
            width: 80,
            render: (_, t) => (
              <Popconfirm title="Thu hồi token này?" onConfirm={() => revoke.mutate(t.id)}>
                <Button size="small" danger>
                  Thu hồi
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />
      <Modal title="Tạo service token (CI)" open={open} onCancel={() => setOpen(false)} footer={null} destroyOnClose>
        {create.error && <Alert type="error" message={errorMessage(create.error)} style={{ marginBottom: 12 }} />}
        <Form layout="vertical" initialValues={{ scopes: ['read_package'], expires_in_days: 365 }} onFinish={(v) => create.mutate(v)}>
          <Form.Item name="name" label="Tên" rules={[{ required: true }]}>
            <Input placeholder="ci-server-x" />
          </Form.Item>
          <Form.Item name="scopes" label="Scope">
            <Select
              mode="multiple"
              options={['read_package', 'read_api', 'read_repository'].map((s) => ({ value: s, label: s }))}
            />
          </Form.Item>
          <Form.Item name="expires_in_days" label="Hết hạn sau (ngày)">
            <InputNumber min={1} max={3650} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={create.isPending}>
            Tạo
          </Button>
        </Form>
      </Modal>
      <Modal title="Token mới — chỉ hiện MỘT lần" open={!!created} onCancel={() => setCreated(null)} onOk={() => setCreated(null)} okText="Đã lưu">
        <Alert type="warning" showIcon message="Copy và lưu vào secret store của CI ngay. Đóng hộp thoại là không xem lại được." style={{ marginBottom: 12 }} />
        {created && <CopyCommand value={created.token} secret />}
      </Modal>
    </>
  )
}
