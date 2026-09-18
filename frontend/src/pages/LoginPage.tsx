import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { errorMessage } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

export default function LoginPage() {
  const { user, login, loading } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  if (!loading && user) return <Navigate to={from} replace />

  return (
    <div className="login-page">
      <Card style={{ width: '100%', maxWidth: 400 }} styles={{ body: { padding: 32 } }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <img src="/favicon.svg" alt="" width={52} height={52} />
          <Typography.Title level={3} style={{ margin: '12px 0 4px' }}>
            HAWEE Tool Hub
          </Typography.Title>
          <Typography.Text type="secondary">Git · Build · Package Registry nội bộ</Typography.Text>
        </div>
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
        <Form
          layout="vertical"
          requiredMark={false}
          onFinish={async (v: { login: string; password: string }) => {
            setSubmitting(true)
            setError(null)
            try {
              await login(v.login, v.password)
              navigate(from, { replace: true })
            } catch (e) {
              setError(errorMessage(e))
            } finally {
              setSubmitting(false)
            }
          }}
        >
          <Form.Item name="login" label="Tên đăng nhập hoặc email" rules={[{ required: true, message: 'Nhập tên đăng nhập' }]}>
            <Input prefix={<UserOutlined />} autoComplete="username" autoFocus size="large" />
          </Form.Item>
          <Form.Item name="password" label="Mật khẩu" rules={[{ required: true, message: 'Nhập mật khẩu' }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={submitting}>
            Đăng nhập
          </Button>
        </Form>
        <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0, fontSize: 12, textAlign: 'center' }}>
          Chưa có tài khoản? Liên hệ quản trị viên hệ thống.
        </Typography.Paragraph>
      </Card>
    </div>
  )
}
