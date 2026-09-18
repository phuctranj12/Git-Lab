import { useEffect, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Collapse,
  Form,
  Input,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  CloudDownloadOutlined,
  CodeOutlined,
  DeleteOutlined,
  InfoCircleOutlined,
  LockOutlined,
  PlusOutlined,
  StopOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { api, errorMessage } from '@/api/client'
import type { SshKey, Token, TokenCreated } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { CopyCommand, PageHeader, TimeAgo } from '@/components/common'

const SECTION = { token: 'token', git: 'git-key', password: 'password' } as const

const BASIC_SCOPE = 'read_package'
const SCOPE_LABEL: Record<string, string> = {
  read_package: 'Tải thư viện',
  read_api: 'Xem dữ liệu qua API',
  write_api: 'Thay đổi dữ liệu qua API',
  read_repository: 'Tải mã nguồn',
  write_repository: 'Gửi mã nguồn',
}
const ADVANCED_SCOPES = [
  { value: 'read_api', label: 'Xem dữ liệu qua API — dùng cho script tự động đọc thông tin' },
  { value: 'write_api', label: 'Thay đổi dữ liệu qua API — tạo build, ẩn phiên bản…' },
  { value: 'read_repository', label: 'Tải mã nguồn' },
  { value: 'write_repository', label: 'Gửi mã nguồn' },
]
const EXPIRY_OPTIONS = [
  { value: 30, label: '30 ngày' },
  { value: 90, label: '3 tháng' },
  { value: 180, label: '6 tháng' },
  { value: 365, label: '1 năm (khuyên dùng)' },
  { value: 0, label: 'Không bao giờ hết hạn' },
]

type Os = 'windows' | 'mac'

function SectionCard({
  id,
  icon,
  title,
  audience,
  description,
  extra,
  children,
}: {
  id: string
  icon: ReactNode
  title: string
  audience: ReactNode
  description: ReactNode
  extra?: ReactNode
  children: ReactNode
}) {
  return (
    <section id={id} className="access-section" aria-labelledby={`${id}-title`}>
      <Card>
        <div className="access-section-head">
          <span className="access-icon" aria-hidden>
            {icon}
          </span>
          <div className="access-section-text">
            <Space wrap size={8} align="center">
              <Typography.Title level={4} id={`${id}-title`} style={{ margin: 0 }}>
                {title}
              </Typography.Title>
              {audience}
            </Space>
            <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
              {description}
            </Typography.Paragraph>
          </div>
          {extra && <div className="access-section-extra">{extra}</div>}
        </div>
        {children}
      </Card>
    </section>
  )
}

function Hint({ children }: { children: ReactNode }) {
  return (
    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
      {children}
    </Typography.Text>
  )
}

function ColumnTitle({ title, hint }: { title: string; hint: string }) {
  return (
    <Tooltip title={hint}>
      <span className="col-hint">
        {title} <InfoCircleOutlined aria-hidden />
      </span>
    </Tooltip>
  )
}

function Intro() {
  const tasks = [
    {
      id: SECTION.token,
      icon: <CloudDownloadOutlined />,
      title: 'Tải thư viện nội bộ về máy',
      who: 'Ai cũng cần',
      color: 'green',
      desc: 'Tạo token để lệnh pip install tải được thư viện của công ty.',
    },
    {
      id: SECTION.git,
      icon: <CodeOutlined />,
      title: 'Đưa mã nguồn tool lên hệ thống',
      who: 'Người phát triển tool',
      color: 'blue',
      desc: 'Cài khóa truy cập Git để gửi (push) và lấy (clone) mã nguồn.',
    },
    {
      id: SECTION.password,
      icon: <LockOutlined />,
      title: 'Đổi mật khẩu đăng nhập',
      who: 'Tài khoản của bạn',
      color: 'default',
      desc: 'Thay mật khẩu vào trang Tool Hub này.',
    },
  ]
  return (
    <Card className="access-intro">
      <Typography.Text strong>Dùng trang này khi bạn muốn:</Typography.Text>
      <div className="task-grid" role="list">
        {tasks.map((t) => (
          <a key={t.id} href={`#${t.id}`} className="task-card" role="listitem">
            <span className="task-icon" aria-hidden>
              {t.icon}
            </span>
            <span className="task-body">
              <span className="task-title">{t.title}</span>
              <span className="task-desc">{t.desc}</span>
              <Tag color={t.color} style={{ marginTop: 6, width: 'fit-content' }}>
                {t.who}
              </Tag>
            </span>
          </a>
        ))}
      </div>
      <Alert
        type="info"
        showIcon
        style={{ marginTop: 16 }}
        message={
          <>
            Nếu bạn chỉ muốn <b>tìm và cài thư viện</b>, bạn chỉ cần phần <a href={`#${SECTION.token}`}>Token tải thư viện</a>.
            <br />
            Nếu bạn là <b>người phát triển tool</b> và muốn đưa mã nguồn lên (git push), bạn cần thêm phần{' '}
            <a href={`#${SECTION.git}`}>Khóa truy cập Git</a>.
          </>
        }
      />
    </Card>
  )
}

function ExpiryCell({ value }: { value: string | null }) {
  if (!value) return <Typography.Text type="secondary">Không hết hạn</Typography.Text>
  const d = dayjs(value)
  const days = d.diff(dayjs(), 'day')
  const color = days < 0 ? 'red' : days <= 14 ? 'orange' : undefined
  return (
    <Tooltip title={d.format('DD/MM/YYYY HH:mm')}>
      <span>
        {d.format('DD/MM/YYYY')}
        {color && (
          <Tag color={color} style={{ marginInlineStart: 6 }}>
            {days < 0 ? 'Đã hết hạn' : `Còn ${days} ngày`}
          </Tag>
        )}
      </span>
    </Tooltip>
  )
}

function LastUsed({ value }: { value: string | null }) {
  return value ? <TimeAgo value={value} /> : <Typography.Text type="secondary">Chưa dùng lần nào</Typography.Text>
}

function PipSetup({ token }: { token?: string }) {
  const { meta } = useAuth()
  const [os, setOs] = useState<Os>('windows')
  const indexUrl = meta?.package_index_url ?? ''
  const host = indexUrl.replace(/^https?:\/\//, '').split('/')[0]
  const netrcPath = os === 'windows' ? '%USERPROFILE%\\_netrc' : '~/.netrc'
  const secret = token ?? '<token-của-bạn>'
  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Segmented<Os>
        value={os}
        onChange={setOs}
        options={[
          { label: 'Windows', value: 'windows' },
          { label: 'macOS / Linux', value: 'mac' },
        ]}
      />
      <div>
        <Typography.Text strong>1. Chỉ cho pip biết kho thư viện của công ty</Typography.Text>
        <CopyCommand value={`pip config set global.index-url ${indexUrl}`} />
      </div>
      <div>
        <Typography.Text strong>
          2. Lưu token vào file <code>{netrcPath}</code>
        </Typography.Text>
        <div>
          <Hint>Mở (hoặc tạo mới) file trên bằng Notepad, dán dòng sau vào và lưu lại.</Hint>
        </div>
        <CopyCommand value={`machine ${host} login __token__ password ${secret}`} secret={!!token} />
      </div>
      <div>
        <Typography.Text strong>3. Cài thư viện như bình thường</Typography.Text>
        <CopyCommand value={`pip install ${meta?.internal_package_prefix ?? 'hawee-'}ten-thu-vien`} />
      </div>
    </Space>
  )
}

function LibraryTokens() {
  const { message } = App.useApp()
  const { meta } = useAuth()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [created, setCreated] = useState<TokenCreated | null>(null)
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: () => api.get<Token[]>('me/tokens') })
  const create = useMutation({
    mutationFn: (v: { name: string; extra_scopes?: string[]; expiry: number }) =>
      api.post<TokenCreated>('me/tokens', {
        name: v.name,
        scopes: [BASIC_SCOPE, ...(v.extra_scopes ?? [])],
        expires_in_days: v.expiry || undefined,
      }),
    onSuccess: (t) => {
      setOpen(false)
      setCreated(t)
      qc.invalidateQueries({ queryKey: ['tokens'] })
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => api.del(`me/tokens/${id}`),
    onSuccess: () => {
      message.success('Đã vô hiệu hóa token')
      qc.invalidateQueries({ queryKey: ['tokens'] })
    },
    onError: (e) => message.error(errorMessage(e)),
  })
  const hasToken = (tokens.data ?? []).some((t) => !t.revoked_at && t.scopes.includes(BASIC_SCOPE))

  return (
    <SectionCard
      id={SECTION.token}
      icon={<CloudDownloadOutlined />}
      title="Token tải thư viện"
      audience={<Tag color="green">Ai cũng cần</Tag>}
      description={
        <>
          Hiểu đơn giản là <b>một mật khẩu riêng dành cho pip</b>: giúp máy bạn tải thư viện nội bộ mà không cần dùng
          mật khẩu đăng nhập web. Mỗi người chỉ cần <b>một</b> token cho tất cả thư viện.
        </>
      }
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          Tạo token
        </Button>
      }
    >
      <div className="field-block">
        <Typography.Text strong>Địa chỉ kho thư viện nội bộ</Typography.Text>
        <div>
          <Hint>Nơi pip tìm thư viện của công ty. Thường chỉ cần cấu hình một lần trên mỗi máy.</Hint>
        </div>
        <CopyCommand value={meta?.package_index_url ?? '—'} />
      </div>

      {!tokens.isLoading && !hasToken && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="Bạn chưa có token tải thư viện"
          description="Bấm “Tạo token” ở trên, hệ thống sẽ hướng dẫn tiếp từng bước."
        />
      )}

      <Table<Token>
        size="middle"
        rowKey="id"
        loading={tokens.isLoading}
        dataSource={tokens.data}
        pagination={false}
        locale={{ emptyText: 'Chưa có token nào' }}
        scroll={{ x: 720 }}
        rowClassName={(t) => (t.revoked_at ? 'row-muted' : '')}
        columns={[
          {
            title: 'Tên',
            render: (_, t) => (
              <Space direction="vertical" size={0}>
                <Typography.Text strong>{t.name}</Typography.Text>
                <Tooltip title="Vài ký tự đầu của token, giúp bạn nhận ra token nào đang dùng ở đâu">
                  <Typography.Text type="secondary" className="mono" style={{ fontSize: 12 }}>
                    {t.token_prefix}…
                  </Typography.Text>
                </Tooltip>
              </Space>
            ),
          },
          {
            title: <ColumnTitle title="Quyền của token" hint="Token được phép làm những việc gì" />,
            render: (_, t) => (
              <Space size={[4, 4]} wrap>
                {t.scopes.map((s) => (
                  <Tag key={s} color={s === BASIC_SCOPE ? 'green' : 'blue'}>
                    {SCOPE_LABEL[s] ?? s}
                  </Tag>
                ))}
              </Space>
            ),
          },
          {
            title: <ColumnTitle title="Ngày hết hạn" hint="Sau ngày này token sẽ không dùng được nữa" />,
            render: (_, t) => <ExpiryCell value={t.expires_at} />,
          },
          {
            title: <ColumnTitle title="Lần sử dụng gần nhất" hint="Giúp biết token còn được dùng hay đã bỏ lâu" />,
            render: (_, t) => <LastUsed value={t.last_used_at} />,
          },
          {
            title: '',
            width: 140,
            align: 'right',
            render: (_, t) =>
              t.revoked_at ? (
                <Tag>Đã vô hiệu hóa</Tag>
              ) : (
                <Popconfirm
                  title="Vô hiệu hóa token này?"
                  description="Token sẽ ngừng hoạt động ngay lập tức. Máy nào đang dùng sẽ không tải được thư viện nữa."
                  okText="Vô hiệu hóa"
                  cancelText="Giữ lại"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => revoke.mutate(t.id)}
                >
                  <Button size="small" danger icon={<StopOutlined />}>
                    Vô hiệu hóa
                  </Button>
                </Popconfirm>
              ),
          },
        ]}
      />

      <Collapse
        ghost
        style={{ marginTop: 12 }}
        items={[
          {
            key: 'howto',
            label: <Typography.Text strong>Đã có token rồi? Xem lại cách cấu hình pip</Typography.Text>,
            children: <PipSetup />,
          },
        ]}
      />

      <Modal title="Tạo token tải thư viện" open={open} onCancel={() => setOpen(false)} footer={null} destroyOnClose>
        {create.error && <Alert type="error" showIcon message={errorMessage(create.error)} style={{ marginBottom: 12 }} />}
        <Form
          layout="vertical"
          requiredMark={false}
          initialValues={{ name: 'pip trên laptop công ty', expiry: 365, extra_scopes: [] }}
          onFinish={(v) => create.mutate(v)}
        >
          <Form.Item
            name="name"
            label="Tên dễ nhớ"
            extra="Để sau này bạn biết token này đang dùng ở máy nào."
            rules={[{ required: true, message: 'Hãy đặt tên cho token' }]}
          >
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="expiry" label="Ngày hết hạn" extra="Hết hạn thì chỉ cần tạo token mới.">
            <Select options={EXPIRY_OPTIONS} />
          </Form.Item>
          <Form.Item label="Quyền của token" style={{ marginBottom: 8 }}>
            <Tag color="green">Tải thư viện</Tag>
            <Hint>luôn có sẵn — đủ dùng cho hầu hết mọi người.</Hint>
          </Form.Item>
          <Collapse
            size="small"
            style={{ marginBottom: 16 }}
            items={[
              {
                key: 'adv',
                label: 'Quyền nâng cao (chỉ dành cho người phát triển)',
                children: (
                  <Form.Item name="extra_scopes" style={{ marginBottom: 0 }}>
                    <Checkbox.Group options={ADVANCED_SCOPES} style={{ display: 'grid', gap: 8 }} />
                  </Form.Item>
                ),
              },
            ]}
          />
          <Button type="primary" htmlType="submit" block loading={create.isPending}>
            Tạo token
          </Button>
        </Form>
      </Modal>

      <Modal
        title="Token đã được tạo"
        open={!!created}
        onCancel={() => setCreated(null)}
        onOk={() => setCreated(null)}
        okText="Tôi đã lưu token"
        cancelButtonProps={{ style: { display: 'none' } }}
        width={680}
        maskClosable={false}
      >
        <Alert
          type="warning"
          showIcon
          message="Token chỉ hiện đúng một lần"
          description="Hãy làm theo các bước bên dưới ngay. Đóng hộp thoại này là không xem lại được — khi đó chỉ cần tạo token mới."
          style={{ marginBottom: 16 }}
        />
        {created && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <div>
              <Typography.Text strong>Token của bạn</Typography.Text>
              <CopyCommand value={created.token} secret />
            </div>
            {created.scopes.includes(BASIC_SCOPE) && meta && <PipSetup token={created.token} />}
          </Space>
        )}
      </Modal>
    </SectionCard>
  )
}

function GitKeys() {
  const { message } = App.useApp()
  const { meta, user } = useAuth()
  const qc = useQueryClient()
  const [form] = Form.useForm()
  const [os, setOs] = useState<Os>('windows')
  const keys = useQuery({ queryKey: ['ssh-keys'], queryFn: () => api.get<SshKey[]>('me/ssh-keys') })
  const add = useMutation({
    mutationFn: (v: { title: string; public_key: string }) =>
      api.post<SshKey>('me/ssh-keys', { title: v.title, public_key: v.public_key.trim() }),
    onSuccess: (k) => {
      message.success(`Đã thêm khóa cho “${k.title}”`)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['ssh-keys'] })
    },
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`me/ssh-keys/${id}`),
    onSuccess: () => {
      message.success('Đã xóa khóa')
      qc.invalidateQueries({ queryKey: ['ssh-keys'] })
    },
    onError: (e) => message.error(errorMessage(e)),
  })
  const sshTarget = meta
    ? meta.git_ssh_port === 22
      ? `${meta.git_ssh_user}@${meta.git_ssh_host}`
      : `-p ${meta.git_ssh_port} ${meta.git_ssh_user}@${meta.git_ssh_host}`
    : 'git@git.hawee.local'
  const copyPubCmd =
    os === 'windows'
      ? 'Get-Content $env:USERPROFILE\\.ssh\\id_ed25519.pub | Set-Clipboard'
      : 'cat ~/.ssh/id_ed25519.pub'

  return (
    <SectionCard
      id={SECTION.git}
      icon={<CodeOutlined />}
      title="Khóa truy cập Git"
      audience={<Tag color="blue">Dành cho người phát triển tool</Tag>}
      description={
        <>
          Dùng để xác nhận <b>“máy tính này là của bạn”</b> khi gửi hoặc lấy mã nguồn. Chỉ cần cài <b>một lần trên mỗi
          máy</b>. Nếu bạn chỉ cài thư viện thì không cần phần này.
        </>
      }
    >
      <Segmented<Os>
        value={os}
        onChange={setOs}
        style={{ marginBottom: 16 }}
        options={[
          { label: 'Windows', value: 'windows' },
          { label: 'macOS / Linux', value: 'mac' },
        ]}
      />
      <Steps
        direction="vertical"
        size="small"
        current={-1}
        className="setup-steps"
        items={[
          {
            title: 'Tạo khóa trên máy của bạn',
            description: (
              <>
                <Hint>
                  Mở {os === 'windows' ? 'PowerShell' : 'Terminal'}, chạy lệnh sau rồi nhấn Enter cho tới khi xong (không cần
                  nhập gì thêm). Nếu máy đã có khóa thì bỏ qua bước này.
                </Hint>
                <CopyCommand value={`ssh-keygen -t ed25519 -C "${user?.email ?? 'email@hawee.com.vn'}"`} />
              </>
            ),
          },
          {
            title: 'Copy khóa công khai của máy',
            description: (
              <>
                <Hint>
                  {os === 'windows'
                    ? 'Lệnh này tự copy khóa vào bộ nhớ tạm, bạn chỉ việc dán ở bước 3.'
                    : 'Lệnh này in khóa ra màn hình, hãy copy toàn bộ dòng đó.'}
                </Hint>
                <CopyCommand value={copyPubCmd} />
              </>
            ),
          },
          {
            title: 'Dán khóa vào ô bên dưới và bấm “Thêm khóa”',
            description: (
              <Form form={form} layout="vertical" requiredMark={false} onFinish={(v) => add.mutate(v)} className="key-form">
                {add.error && <Alert type="error" showIcon message={errorMessage(add.error)} style={{ marginBottom: 12 }} />}
                <Form.Item
                  name="title"
                  label="Tên máy / tên dễ nhớ"
                  extra="Ví dụ: Laptop công ty, PC văn phòng. Chỉ để bạn biết khóa này thuộc máy nào."
                  rules={[{ required: true, message: 'Hãy đặt tên cho máy này' }]}
                >
                  <Input placeholder="Laptop công ty" maxLength={100} />
                </Form.Item>
                <Form.Item
                  name="public_key"
                  label="Khóa công khai của máy"
                  extra={
                    <>
                      Là một dòng bắt đầu bằng <code>ssh-ed25519</code> hoặc <code>ssh-rsa</code>. Tuyệt đối{' '}
                      <b>không dán khóa bí mật</b> (file không có đuôi <code>.pub</code>).
                    </>
                  }
                  rules={[
                    { required: true, message: 'Hãy dán khóa công khai' },
                    {
                      validator: (_, v: string | undefined) =>
                        v && /PRIVATE KEY/.test(v)
                          ? Promise.reject(
                              new Error('Đây là khóa BÍ MẬT — không được đưa lên. Hãy dùng nội dung file có đuôi .pub'),
                            )
                          : Promise.resolve(),
                    },
                  ]}
                >
                  <Input.TextArea rows={3} placeholder="ssh-ed25519 AAAAC3Nza… ten@may-tinh" className="mono" />
                </Form.Item>
                <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={add.isPending}>
                  Thêm khóa
                </Button>
              </Form>
            ),
          },
          {
            title: 'Kiểm tra máy đã kết nối Git chưa',
            description: (
              <>
                <Hint>
                  Chạy lệnh sau. Nếu thấy dòng <b>“xác thực SSH thành công ✓”</b> là máy đã được hệ thống nhận diện. Lần
                  đầu có thể được hỏi “Are you sure you want to continue connecting?” — gõ <code>yes</code> rồi Enter.
                </Hint>
                <CopyCommand value={`ssh -T ${sshTarget}`} />
              </>
            ),
          },
        ]}
      />

      <Typography.Title level={5} style={{ marginTop: 8 }}>
        Các máy đã được cài khóa
      </Typography.Title>
      <Table<SshKey>
        size="middle"
        rowKey="id"
        loading={keys.isLoading}
        dataSource={keys.data}
        pagination={false}
        locale={{ emptyText: 'Chưa có máy nào được cài khóa' }}
        scroll={{ x: 600 }}
        columns={[
          { title: 'Tên máy', render: (_, k) => <Typography.Text strong>{k.title}</Typography.Text> },
          {
            title: (
              <ColumnTitle
                title="Mã nhận diện khóa"
                hint="Hệ thống tự tạo để phân biệt các khóa. Bạn không cần quan tâm tới mã này."
              />
            ),
            render: (_, k) => (
              <Typography.Text type="secondary" className="mono" style={{ fontSize: 12 }} ellipsis={{ tooltip: k.fingerprint }}>
                {k.fingerprint}
              </Typography.Text>
            ),
          },
          {
            title: <ColumnTitle title="Lần sử dụng gần nhất" hint="Cho biết khóa này có còn đang được dùng không" />,
            render: (_, k) => <LastUsed value={k.last_used_at} />,
          },
          {
            title: '',
            width: 90,
            align: 'right',
            render: (_, k) => (
              <Popconfirm
                title={`Xóa khóa của “${k.title}”?`}
                description="Máy này sẽ không gửi/lấy mã nguồn được nữa cho tới khi cài lại khóa."
                okText="Xóa"
                cancelText="Giữ lại"
                okButtonProps={{ danger: true }}
                onConfirm={() => remove.mutate(k.id)}
              >
                <Button size="small" danger icon={<DeleteOutlined />}>
                  Xóa
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />
    </SectionCard>
  )
}

function ChangePassword() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const { refreshUser } = useAuth()
  const change = useMutation({
    mutationFn: (v: { current_password: string; new_password: string }) =>
      api.post('auth/change-password', { current_password: v.current_password, new_password: v.new_password }),
    onSuccess: async () => {
      message.success('Đã đổi mật khẩu — vui lòng đăng nhập lại')
      await refreshUser().catch(() => undefined)
      navigate('/login')
    },
  })
  return (
    <SectionCard
      id={SECTION.password}
      icon={<LockOutlined />}
      title="Đổi mật khẩu đăng nhập"
      audience={<Tag>Tài khoản của bạn</Tag>}
      description="Chỉ thay mật khẩu vào trang Tool Hub. Không ảnh hưởng tới khóa truy cập Git hay token tải thư viện đang dùng."
    >
      {change.error && <Alert type="error" showIcon message={errorMessage(change.error)} style={{ marginBottom: 12 }} />}
      <Form layout="vertical" requiredMark={false} onFinish={(v) => change.mutate(v)} className="password-form">
        <Form.Item name="current_password" label="Mật khẩu hiện tại" rules={[{ required: true, message: 'Nhập mật khẩu hiện tại' }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item
          name="new_password"
          label="Mật khẩu mới"
          extra="Tối thiểu 8 ký tự."
          rules={[{ required: true, message: 'Nhập mật khẩu mới' }, { min: 8, message: 'Tối thiểu 8 ký tự' }]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm"
          label="Nhập lại mật khẩu mới"
          dependencies={['new_password']}
          rules={[
            { required: true, message: 'Nhập lại mật khẩu mới' },
            ({ getFieldValue }) => ({
              validator: (_, v) =>
                !v || v === getFieldValue('new_password') ? Promise.resolve() : Promise.reject(new Error('Hai mật khẩu chưa khớp')),
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={change.isPending}>
          Đổi mật khẩu
        </Button>
      </Form>
    </SectionCard>
  )
}

export default function SettingsPage() {
  const { hash } = useLocation()
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [hash])

  return (
    <div className="access-page">
      <PageHeader
        title="Cài đặt truy cập"
        subtitle="Thiết lập để tải thư viện nội bộ, đưa mã nguồn tool lên hệ thống và quản lý mật khẩu."
      />
      <Space direction="vertical" size={20} style={{ width: '100%' }}>
        <Intro />
        <LibraryTokens />
        <GitKeys />
        <ChangePassword />
      </Space>
    </div>
  )
}
