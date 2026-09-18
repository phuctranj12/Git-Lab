import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Col, List, Row, Skeleton, Space, Tag, Typography } from 'antd'
import { FireOutlined, RocketOutlined, WarningOutlined } from '@ant-design/icons'
import { api, errorMessage } from '@/api/client'
import type { Dashboard } from '@/api/types'
import { BuildTable } from '@/components/BuildTable'
import { useAuth } from '@/auth/AuthContext'
import { BuildStatusTag, CopyCommand, EmptyState, PageHeader, RoleTag, TimeAgo } from '@/components/common'

export default function DashboardPage() {
  const { user, meta } = useAuth()
  const q = useQuery({ queryKey: ['dashboard'], queryFn: () => api.get<Dashboard>('dashboard'), refetchInterval: 15000 })
  const d = q.data

  return (
    <>
      <PageHeader title={`Xin chào, ${user?.full_name || user?.username}`} subtitle="Tổng quan tool, build và package nội bộ" />
      {q.error && <Alert type="error" message={errorMessage(q.error)} style={{ marginBottom: 16 }} />}
      {meta && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Typography.Text strong>Cấu hình pip một lần — mọi package (nội bộ + PyPI) đi qua một index duy nhất:</Typography.Text>
            <CopyCommand value={`pip config set global.index-url ${meta.package_index_url}`} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Cần có token tải thư viện — tạo ở <Link to="/settings#token">Cài đặt truy cập</Link>.
            </Typography.Text>
          </Space>
        </Card>
      )}
      <Skeleton loading={q.isLoading} active>
        {d && (
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={16}>
              <Card title="Project của tôi" extra={<Link to="/tools">Xem tất cả</Link>}>
                {d.my_projects.length === 0 ? (
                  <EmptyState title="Bạn chưa là thành viên project nào" />
                ) : (
                  <List
                    dataSource={d.my_projects}
                    renderItem={(p) => (
                      <List.Item extra={<BuildStatusTag status={p.last_build_status} />}>
                        <List.Item.Meta
                          title={<Link to={`/p/${p.full_path}`}>{p.name}</Link>}
                          description={
                            <Space size={8} wrap>
                              <span>{p.full_path}</span>
                              {p.package_name && <Tag>{p.package_name}</Tag>}
                              {p.latest_release_version && <Tag color="green">v{p.latest_release_version}</Tag>}
                            </Space>
                          }
                        />
                      </List.Item>
                    )}
                  />
                )}
              </Card>
            </Col>
            <Col xs={24} xl={8}>
              <Card title="Group của tôi" extra={<Link to="/groups">Tất cả group</Link>}>
                {d.my_groups.length === 0 ? (
                  <EmptyState title="Chưa thuộc group nào" />
                ) : (
                  <List
                    size="small"
                    dataSource={d.my_groups}
                    renderItem={(g) => (
                      <List.Item extra={<RoleTag role={g.role} />}>
                        <Link to={`/groups/${g.slug}`}>{g.name}</Link>
                        <Typography.Text type="secondary"> · {g.project_count} project</Typography.Text>
                      </List.Item>
                    )}
                  />
                )}
              </Card>
            </Col>
            <Col xs={24} xl={16}>
              <Card title="Build gần đây" extra={<Link to="/builds">Tất cả build</Link>}>
                {d.recent_builds.length ? <BuildTable builds={d.recent_builds} compact /> : <EmptyState title="Chưa có build" />}
              </Card>
            </Col>
            <Col xs={24} xl={8}>
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Card title={<Space><RocketOutlined />Release mới</Space>}>
                  {d.recent_releases.length === 0 ? (
                    <EmptyState title="Chưa có release" />
                  ) : (
                    <List
                      size="small"
                      dataSource={d.recent_releases}
                      renderItem={(r) => (
                        <List.Item extra={<TimeAgo value={r.created_at} />}>
                          <Link to={`/p/${r.project_path}/versions`}>{r.package_name}</Link>{' '}
                          <Tag color={r.is_yanked ? 'red' : 'green'}>{r.version}</Tag>
                        </List.Item>
                      )}
                    />
                  )}
                </Card>
                <Card title={<Space><WarningOutlined style={{ color: '#cf1322' }} />Build lỗi</Space>}>
                  {d.failed_builds.length === 0 ? (
                    <Typography.Text type="secondary">Không có build lỗi gần đây 🎉</Typography.Text>
                  ) : (
                    <List
                      size="small"
                      dataSource={d.failed_builds}
                      renderItem={(b) => (
                        <List.Item>
                          <Space direction="vertical" size={0}>
                            <Link to={`/builds/${b.id}`}>
                              {b.project_path} #{b.number}
                            </Link>
                            <Typography.Text type="danger" style={{ fontSize: 12 }}>
                              {b.error_code}
                            </Typography.Text>
                          </Space>
                        </List.Item>
                      )}
                    />
                  )}
                </Card>
                <Card title={<Space><FireOutlined style={{ color: '#fa541c' }} />Package phổ biến</Space>}>
                  {d.popular_packages.length === 0 ? (
                    <EmptyState title="Chưa có package" />
                  ) : (
                    <List
                      size="small"
                      dataSource={d.popular_packages}
                      renderItem={(p) => (
                        <List.Item extra={<Typography.Text type="secondary">{p.downloads} lượt tải</Typography.Text>}>
                          <Link to={`/packages/${p.package_name}`}>{p.package_name}</Link>
                          {p.latest_version && <Tag style={{ marginLeft: 6 }}>{p.latest_version}</Tag>}
                        </List.Item>
                      )}
                    />
                  )}
                </Card>
              </Space>
            </Col>
          </Row>
        )}
      </Skeleton>
    </>
  )
}
