import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Card, Col, Descriptions, Row, Skeleton, Space, Steps, Typography } from 'antd'
import { api } from '@/api/client'
import type { Project } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import Markdown from '@/components/Markdown'
import { BuildStatusTag, CopyCommand, ShortSha, TimeAgo, formatBytes } from '@/components/common'

export default function OverviewTab({ project: p }: { project: Project }) {
  const { meta } = useAuth()
  const readme = useQuery({
    queryKey: ['readme', p.full_path, p.latest_commit_sha],
    queryFn: () => api.get<{ filename: string | null; content: string | null }>(`projects/${p.full_path}/readme`),
  })
  const empty = !p.latest_commit_sha

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={16}>
        {empty ? (
          <Card title="Repository đang trống — bắt đầu thế nào?">
            <Steps
              direction="vertical"
              size="small"
              items={[
                {
                  title: 'Thêm SSH public key',
                  description: (
                    <span>
                      Vào <Link to="/settings">SSH key & token</Link>, dán nội dung <code>~/.ssh/id_ed25519.pub</code>.
                    </span>
                  ),
                },
                { title: 'Clone repository', description: <CopyCommand value={`git clone ${p.clone_url}`} /> },
                {
                  title: 'Tạo pyproject.toml + README.md',
                  description: (
                    <pre className="markdown-body" style={{ background: '#f6f8fa', padding: 10, borderRadius: 6, fontSize: 12, overflow: 'auto' }}>
{`[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "${p.package_name ?? `${meta?.internal_package_prefix ?? 'hawee-'}${p.slug}`}"
version = "0.1.0"
requires-python = ">=3.10"
readme = "README.md"`}
                    </pre>
                  ),
                },
                {
                  title: 'Push & release',
                  description: (
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <CopyCommand value={`git add . && git commit -m "init" && git push origin ${p.default_branch}`} />
                      <CopyCommand value="git tag v0.1.0 && git push origin v0.1.0" />
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        Push nhánh → validation build. Push tag vX.Y.Z (khớp version trong pyproject) → release build + publish.
                      </Typography.Text>
                    </Space>
                  ),
                },
              ]}
            />
          </Card>
        ) : (
          <Card title={readme.data?.filename ?? 'README'}>
            <Skeleton loading={readme.isLoading} active>
              {readme.data?.content ? (
                <Markdown source={readme.data.content} />
              ) : (
                <Typography.Text type="secondary">Repository chưa có README.</Typography.Text>
              )}
            </Skeleton>
          </Card>
        )}
      </Col>
      <Col xs={24} lg={8}>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {p.install_command && (
            <Card size="small" title="Cài đặt">
              <Space direction="vertical" style={{ width: '100%' }}>
                <CopyCommand value={p.latest_release_version ? `${p.install_command}==${p.latest_release_version}` : p.install_command} />
                {!p.latest_release_version && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    Chưa có release — push tag vX.Y.Z để publish.
                  </Typography.Text>
                )}
                <Link to={`/p/${p.full_path}/packages`}>Hướng dẫn cấu hình pip →</Link>
              </Space>
            </Card>
          )}
          <Card size="small" title="Clone">
            <CopyCommand value={`git clone ${p.clone_url}`} />
          </Card>
          <Card size="small" title="Thông tin">
            <Descriptions column={1} size="small" colon={false} labelStyle={{ color: '#8c8c8c', width: 130 }}>
              <Descriptions.Item label="Mô tả">{p.description || '—'}</Descriptions.Item>
              <Descriptions.Item label="Latest release">{p.latest_release_version ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="Python">{p.python_requires ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="Build gần nhất">
                <BuildStatusTag status={p.last_build_status} />
              </Descriptions.Item>
              <Descriptions.Item label="Commit mới nhất">
                <ShortSha sha={p.latest_commit_sha} />
              </Descriptions.Item>
              <Descriptions.Item label="Nhánh mặc định">{p.default_branch}</Descriptions.Item>
              <Descriptions.Item label="Dung lượng repo">{formatBytes(p.repository_size_bytes)}</Descriptions.Item>
              <Descriptions.Item label="Owner">
                <Link to={`/groups/${p.group_slug}`}>{p.group_name}</Link>
              </Descriptions.Item>
              <Descriptions.Item label="Cập nhật">
                <TimeAgo value={p.updated_at} />
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Space>
      </Col>
    </Row>
  )
}
