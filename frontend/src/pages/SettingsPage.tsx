import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Checkbox, Col, Form, Input, InputNumber, Modal, Popconfirm, Row, Space, Table, Tag, Typography } from 'antd'
import { KeyOutlined, PlusOutlined } from '@ant-design/icons'
import { api, errorMessage } from '@/api/client'
import type { SshKey, Token, TokenCreated } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { CopyCommand, PageHeader, TimeAgo } from '@/components/common'

const SCOPES: { value: string; label: string }[] = [
  { value: 'read_package', label: 'read_package — pip install package nội bộ' },
  { value: 'read_api', label: 'read_api — đọc API' },
  { value: 'write_api', label: 'write_api — gọi API ghi (tạo build, yank…)' },
  { value: 'read_repository', label: 'read_repository' },
  { value: 'write_repository', label: 'write_repository' },
]

function SshKeys() {
  const { message } = App.useApp()
  const { meta } = useAuth()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const keys = useQuery({ queryKey: ['ssh-keys'], queryFn: () => api.get<SshKey[]>('me/ssh-keys') })
  const add = useMutation({
    mutationFn: (v: { title: string; public_key: string }) => api.post<SshKey>('me/ssh-keys', v),
    onSuccess: (k) => {
      message.success(`Đã thêm key ${k.fingerprint}`)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['ssh-keys'] })
    },
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`me/ssh-keys/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ssh-keys'] }),
    onError: (e) => message.error(errorMessage(e)),
  })
  const sshTarget = meta
    ? meta.git_ssh_port === 22
      ? `${meta.git_ssh_user}@${meta.git_ssh_host}`
      : `-p ${meta.git_ssh_port} ${meta.git_ssh_user}@${meta.git_ssh_host}`
    : 'git@git.hawee.local'

  return (
    <Card title={<Space><KeyOutlined />SSH key (git clone/push)</Space>}>
      <Typography.Paragraph type="secondary">
        Chưa có key? Chạy <code>ssh-keygen -t ed25519 -C "email@hawee.com.vn"</code> rồi dán nội dung file{' '}
        <code>~/.ssh/id_ed25519.pub</code>. Không bao giờ dán private key.
      </Typography.Paragraph>
      {add.error && <Alert type="error" message={errorMessage(add.error)} style={{ marginBottom: 12 }} />}
      <Form form={form} layout="vertical" onFinish={(v) => add.mutate(v)}>
        <Form.Item name="title" label="Tên key" rules={[{ required: true }]}>
          <Input placeholder="Laptop công ty" />
        </Form.Item>
        <Form.Item name="public_key" label="Public key" rules={[{ required: true }]}>
          <Input.TextArea rows={3} placeholder="ssh-ed25519 AAAAC3… phuc@pc" className="mono" />
        </Form.Item>
        <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={add.isPending}>
          Thêm key
        </Button>
      </Form>
      <Table<SshKey>
        style={{ marginTop: 16 }}
        size="small"
        rowKey="id"
        loading={keys.isLoading}
        dataSource={keys.data}
        pagination={false}
        locale={{ emptyText: 'Chưa có SSH key' }}
        scroll={{ x: 560 }}
        columns={[
          { title: 'Tên', dataIndex: 'title' },
          { title: 'Fingerprint', render: (_, k) => <Typography.Text code style={{ fontSize: 11 }}>{k.fingerprint}</Typography.Text> },
          { title: 'Dùng lần cuối', render: (_, k) => <TimeAgo value={k.last_used_at} /> },
          {
            title: '',
            width: 70,
            render: (_, k) => (
              <Popconfirm title="Xoá key này?" onConfirm={() => remove.mutate(k.id)}>
                <Button size="small" danger>
                  Xoá
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />
      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 4 }}>
        Kiểm tra kết nối:
      </Typography.Paragraph>
      <CopyCommand value={`ssh -T ${sshTarget}`} />
    </Card>
  )
}

function Tokens() {
  const { message } = App.useApp()
  const { meta } = useAuth()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [created, setCreated] = useState<TokenCreated | null>(null)
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: () => api.get<Token[]>('me/tokens') })
  const create = useMutation({
    mutationFn: (v: { name: string; scopes: string[]; expires_in_days?: number }) => api.post<TokenCreated>('me/tokens', v),
    onSuccess: (t) => {
      setOpen(false)
      setCreated(t)
      qc.invalidateQueries({ queryKey: ['tokens'] })
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => api.del(`me/tokens/${id}`),
    onSuccess: () => {
      message.success('Đã thu hồi token')
      qc.invalidateQueries({ queryKey: ['tokens'] })
    },
    onError: (e) => message.error(errorMessage(e)),
  })
  const host = (meta?.package_index_url ?? '').replace(/^https?:\/\//, '').split('/')[0]

  return (
    <Card
      title="Personal access token"
      extra={
        <Button icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          Tạo token
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        Mỗi người chỉ cần <b>MỘT</b> token <code>read_package</code> cho toàn bộ registry (không tạo token theo từng tool).
        Token lưu dạng hash — chỉ hiện đúng một lần khi tạo.
      </Typography.Paragraph>
      <Table<Token>
        size="small"
        rowKey="id"
        loading={tokens.isLoading}
        dataSource={tokens.data}
        pagination={false}
        locale={{ emptyText: 'Chưa có token' }}
        scroll={{ x: 640 }}
        columns={[
          { title: 'Tên', dataIndex: 'name' },
          { title: 'Tiền tố', render: (_, t) => <code>{t.token_prefix}…</code> },
          { title: 'Scope', render: (_, t) => t.scopes.map((s) => <Tag key={s}>{s}</Tag>) },
          { title: 'Hết hạn', render: (_, t) => (t.expires_at ? <TimeAgo value={t.expires_at} /> : 'Không') },
          { title: 'Dùng lần cuối', render: (_, t) => <TimeAgo value={t.last_used_at} /> },
          {
            title: '',
            width: 90,
            render: (_, t) => (
              <Popconfirm title="Thu hồi token? Mọi nơi đang dùng sẽ mất quyền ngay." onConfirm={() => revoke.mutate(t.id)}>
                <Button size="small" danger>
                  Thu hồi
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />
      <Modal title="Tạo personal access token" open={open} onCancel={() => setOpen(false)} footer={null} destroyOnClose>
        {create.error && <Alert type="error" message={errorMessage(create.error)} style={{ marginBottom: 12 }} />}
        <Form layout="vertical" initialValues={{ name: 'pip', scopes: ['read_package'], expires_in_days: 365 }} onFinish={(v) => create.mutate(v)}>
          <Form.Item name="name" label="Tên" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="scopes" label="Scope" rules={[{ required: true, message: 'Chọn ít nhất một scope' }]}>
            <Checkbox.Group options={SCOPES} style={{ display: 'grid', gap: 6 }} />
          </Form.Item>
          <Form.Item name="expires_in_days" label="Hết hạn sau (ngày) — để trống = không hết hạn">
            <InputNumber min={1} max={3650} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={create.isPending}>
            Tạo token
          </Button>
        </Form>
      </Modal>
      <Modal title="Token mới — chỉ hiện MỘT lần" open={!!created} onCancel={() => setCreated(null)} onOk={() => setCreated(null)} okText="Đã lưu" width={640}>
        <Alert type="warning" showIcon message="Copy token ngay. Đóng hộp thoại là không xem lại được." style={{ marginBottom: 12 }} />
        {created && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <CopyCommand value={created.token} secret />
            {created.scopes.includes('read_package') && meta && (
              <>
                <Typography.Text>Cấu hình pip (một lần):</Typography.Text>
                <CopyCommand value={`pip config set global.index-url ${meta.package_index_url}`} />
                <Typography.Text>
                  Thêm dòng sau vào <code>~/.netrc</code> (Windows: <code>%USERPROFILE%\_netrc</code>):
                </Typography.Text>
                <CopyCommand value={`machine ${host} login __token__ password ${created.token}`} secret />
              </>
            )}
          </Space>
        )}
      </Modal>
    </Card>
  )
}

function ChangePassword() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const { refreshUser } = useAuth()
  const change = useMutation({
    mutationFn: (v: { current_password: string; new_password: string }) => api.post('auth/change-password', v),
    onSuccess: async () => {
      message.success('Đã đổi mật khẩu — vui lòng đăng nhập lại')
      await refreshUser().catch(() => undefined)
      navigate('/login')
    },
  })
  return (
    <Card title="Đổi mật khẩu">
      {change.error && <Alert type="error" message={errorMessage(change.error)} style={{ marginBottom: 12 }} />}
      <Form layout="vertical" onFinish={(v) => change.mutate(v)}>
        <Form.Item name="current_password" label="Mật khẩu hiện tại" rules={[{ required: true }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item name="new_password" label="Mật khẩu mới" rules={[{ required: true }, { min: 8, message: 'Tối thiểu 8 ký tự' }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm"
          label="Nhập lại"
          dependencies={['new_password']}
          rules={[
            { required: true },
            ({ getFieldValue }) => ({
              validator: (_, v) => (v === getFieldValue('new_password') ? Promise.resolve() : Promise.reject(new Error('Không khớp'))),
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={change.isPending}>
          Đổi mật khẩu
        </Button>
      </Form>
    </Card>
  )
}

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="SSH key & token" subtitle="Truy cập Git bằng SSH key, pip/API bằng access token" />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <SshKeys />
        </Col>
        <Col xs={24} xl={12}>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Tokens />
            <ChangePassword />
          </Space>
        </Col>
      </Row>
    </>
  )
}
