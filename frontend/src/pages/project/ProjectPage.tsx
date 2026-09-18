import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Alert, Breadcrumb, Button, Result, Skeleton, Space, Tabs, Tag, Typography } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { api, ApiError, errorMessage } from '@/api/client'
import type { Project } from '@/api/types'
import { BuildStatusTag, RoleTag, VisibilityTag } from '@/components/common'
import OverviewTab from './OverviewTab'
import RepositoryTab from './RepositoryTab'
import VersionsTab from './VersionsTab'
import BuildsTab, { useManualBuild } from './BuildsTab'
import PackagesTab from './PackagesTab'
import MembersTab from './MembersTab'
import SettingsTab from './SettingsTab'

export function useProject(group: string, project: string) {
  return useQuery({
    queryKey: ['project', group, project],
    queryFn: () => api.get<Project>(`projects/${group}/${project}`),
  })
}

export default function ProjectPage() {
  const { group = '', project = '', tab = 'overview' } = useParams()
  const navigate = useNavigate()
  const q = useProject(group, project)
  const manual = useManualBuild(group, project)

  if (q.error) {
    if (q.error instanceof ApiError && q.error.status === 404) {
      return <Result status="404" title="Không tìm thấy project" subTitle="Project không tồn tại hoặc bạn không có quyền xem." extra={<Link to="/tools">Về danh mục tool</Link>} />
    }
    return <Alert type="error" message={errorMessage(q.error)} />
  }
  const p = q.data
  const can = (perm: string) => p?.permissions.includes(perm) ?? false

  const items = p
    ? [
        { key: 'overview', label: 'Tổng quan', children: <OverviewTab project={p} /> },
        { key: 'repository', label: 'Repository', children: <RepositoryTab project={p} /> },
        ...(p.language === 'PYTHON' ? [{ key: 'versions', label: 'Versions', children: <VersionsTab project={p} /> }] : []),
        { key: 'builds', label: 'Builds', children: <BuildsTab project={p} /> },
        ...(p.language === 'PYTHON' ? [{ key: 'packages', label: 'Packages', children: <PackagesTab project={p} /> }] : []),
        { key: 'members', label: 'Thành viên', children: <MembersTab project={p} /> },
        ...(can('project.update') ? [{ key: 'settings', label: 'Cài đặt', children: <SettingsTab project={p} /> }] : []),
      ]
    : []

  return (
    <Skeleton loading={q.isLoading} active>
      {p && (
        <>
          <Breadcrumb
            style={{ marginBottom: 8 }}
            items={[
              { title: <Link to="/tools">Tool</Link> },
              { title: <Link to={`/groups/${p.group_slug}`}>{p.group_name}</Link> },
              { title: p.name },
            ]}
          />
          <div className="page-header">
            <div style={{ minWidth: 0 }}>
              <Space wrap size={8}>
                <Typography.Title level={3} style={{ margin: 0 }}>
                  {p.name}
                </Typography.Title>
                <VisibilityTag value={p.visibility} />
                {p.archived && <Tag>Archived</Tag>}
                {p.latest_release_version && <Tag color="green">v{p.latest_release_version}</Tag>}
                <BuildStatusTag status={p.last_build_status} />
              </Space>
              <div>
                <Typography.Text type="secondary">
                  {p.full_path}
                  {p.package_name && (
                    <>
                      {' · '}
                      <code>{p.package_name}</code>
                    </>
                  )}
                  {' · quyền của bạn: '}
                </Typography.Text>
                <RoleTag role={p.my_level} />
              </div>
            </div>
            {can('build.run') && !p.archived && (
              <Button icon={<PlayCircleOutlined />} loading={manual.isPending} onClick={() => manual.mutate(undefined)}>
                Chạy build
              </Button>
            )}
          </div>
          {p.archived && (
            <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="Project đã archive: repository chỉ đọc, không nhận build mới. Package đã publish vẫn cài được." />
          )}
          <Tabs activeKey={tab} onChange={(k) => navigate(`/p/${group}/${project}${k === 'overview' ? '' : `/${k}`}`)} items={items} destroyInactiveTabPane />
        </>
      )}
    </Skeleton>
  )
}
