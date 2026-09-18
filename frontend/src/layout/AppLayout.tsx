import { useMemo, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Avatar, Button, Dropdown, Layout, Menu, Space, Typography } from 'antd'
import {
  AppstoreOutlined,
  AuditOutlined,
  BuildOutlined,
  DashboardOutlined,
  DesktopOutlined,
  InboxOutlined,
  KeyOutlined,
  LogoutOutlined,
  MenuOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { useAuth } from '@/auth/AuthContext'

const { Header, Sider, Content } = Layout

export default function AppLayout() {
  const { user, logout, meta } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(false)
  const [broken, setBroken] = useState(false)

  const items = useMemo(() => {
    const main = [
      { key: '/', icon: <DashboardOutlined />, label: <Link to="/">Tổng quan</Link> },
      { key: '/tools', icon: <AppstoreOutlined />, label: <Link to="/tools">Danh mục tool</Link> },
      { key: '/packages', icon: <InboxOutlined />, label: <Link to="/packages">Package</Link> },
      { key: '/builds', icon: <BuildOutlined />, label: <Link to="/builds">Build</Link> },
      { key: '/groups', icon: <TeamOutlined />, label: <Link to="/groups">Group</Link> },
      { key: '/settings', icon: <KeyOutlined />, label: <Link to="/settings">SSH key & token</Link> },
    ]
    if (user?.is_system_admin) {
      main.push(
        { type: 'divider' } as never,
        {
          key: 'admin',
          type: 'group',
          label: 'Quản trị',
          children: [
            { key: '/admin/users', icon: <UserOutlined />, label: <Link to="/admin/users">Người dùng</Link> },
            { key: '/admin/audit', icon: <AuditOutlined />, label: <Link to="/admin/audit">Audit log</Link> },
            { key: '/admin/system', icon: <DesktopOutlined />, label: <Link to="/admin/system">Hệ thống</Link> },
          ],
        } as never,
      )
    }
    return main
  }, [user])

  const selected = useMemo(() => {
    const p = location.pathname
    if (p === '/') return ['/']
    const keys = ['/admin/users', '/admin/audit', '/admin/system', '/tools', '/packages', '/builds', '/groups', '/settings']
    if (p.startsWith('/p/')) return ['/tools']
    return [keys.find((k) => p.startsWith(k)) ?? '/']
  }, [location.pathname])

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        breakpoint="lg"
        collapsedWidth={broken ? 0 : 80}
        collapsed={collapsed}
        onBreakpoint={(b) => {
          setBroken(b)
          setCollapsed(b)
        }}
        onCollapse={setCollapsed}
        trigger={null}
        width={232}
        className="app-sider"
      >
        <Link to="/" className="brand">
          <img src="/favicon.svg" alt="" width={32} height={32} />
          {!collapsed && (
            <span>
              HAWEE <b>Tool Hub</b>
            </span>
          )}
        </Link>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={selected}
          items={items}
          onClick={() => broken && setCollapsed(true)}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          <Button type="text" icon={<MenuOutlined />} onClick={() => setCollapsed(!collapsed)} aria-label="Menu" />
          <Space size="middle">
            {meta && (
              <Typography.Text type="secondary" className="hide-mobile">
                Index: <code>{meta.package_index_url}</code>
              </Typography.Text>
            )}
            <Dropdown
              menu={{
                items: [
                  { key: 'settings', icon: <KeyOutlined />, label: 'SSH key & token', onClick: () => navigate('/settings') },
                  { type: 'divider' },
                  {
                    key: 'logout',
                    icon: <LogoutOutlined />,
                    danger: true,
                    label: 'Đăng xuất',
                    onClick: async () => {
                      await logout()
                      navigate('/login')
                    },
                  },
                ],
              }}
            >
              <Space style={{ cursor: 'pointer' }}>
                <Avatar style={{ background: '#1e3a5f' }}>{user?.username.slice(0, 1).toUpperCase()}</Avatar>
                <span className="hide-mobile">{user?.full_name || user?.username}</span>
              </Space>
            </Dropdown>
          </Space>
        </Header>
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
