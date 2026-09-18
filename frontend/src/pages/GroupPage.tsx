import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Col, Form, Modal, Popconfirm, Row, Select, Skeleton, Space, Table, Tabs, Typography } from 'antd'
import { PlusOutlined, UserAddOutlined } from '@ant-design/icons'
import { api, errorMessage } from '@/api/client'
import type { Group, GroupMember, Page, Project } from '@/api/types'
import CreateProjectModal from '@/components/CreateProjectModal'
import { EmptyState, PageHeader, RoleTag, TimeAgo, VisibilityTag } from '@/components/common'
import UserPicker from '@/components/UserPicker'
import { ToolCard } from '@/pages/CataloguePage'

const ROLE_OPTIONS = [
  { value: 'VIEWER', label: 'Viewer' },
  { value: 'DEVELOPER', label: 'Developer' },
  { value: 'MAINTAINER', label: 'Maintainer' },
  { value: 'OWNER', label: 'Owner' },
]

export default function GroupPage() {
  const { slug = '' } = useParams()
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [creating, setCreating] = useState(false)
  const group = useQuery({ queryKey: ['group', slug], queryFn: () => api.get<Group>(`groups/${slug}`) })
  const members = useQuery({ queryKey: ['group-members', slug], queryFn: () => api.get<GroupMember[]>(`groups/${slug}/members`) })
  const projects = useQuery({
    queryKey: ['projects', 'group', slug],
    queryFn: () => api.get<Page<Project>>('projects', { group: slug, page_size: 100 }),
  })
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['group-members', slug] })
    qc.invalidateQueries({ queryKey: ['group', slug] })
  }
  const add = useMutation({
    mutationFn: (v: { user_id: string; role: string }) => api.post(`groups/${slug}/members`, v),
    onSuccess: () => {
      setAdding(false)
      message.success('Đã thêm thành viên')
      invalidate()
    },
  })
  const changeRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) => api.patch(`groups/${slug}/members/${userId}`, { role }),
    onSuccess: () => {
      message.success('Đã đổi role')
      invalidate()
    },
    onError: (e) => message.error(errorMessage(e)),
  })
  const remove = useMutation({
    mutationFn: (userId: string) => api.del(`groups/${slug}/members/${userId}`),
    onSuccess: () => {
      message.success('Đã xoá khỏi group')
      invalidate()
    },
    onError: (e) => message.error(errorMessage(e)),
  })

  if (group.error) return <Alert type="error" message={errorMessage(group.error)} />
  const g = group.data
  const manage = g?.can_manage ?? false

  return (
    <Skeleton loading={group.isLoading} active>
      {g && (
        <>
          <PageHeader
            title={
              <Space>
                {g.name}
                <VisibilityTag value={g.visibility} />
              </Space>
            }
            subtitle={g.description || g.slug}
            extra={
              manage && (
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
                  Tạo project
                </Button>
              )
            }
          />
          <Tabs
            items={[
              {
                key: 'projects',
                label: `Project (${g.project_count})`,
                children:
                  projects.data?.items.length === 0 ? (
                    <EmptyState title="Group chưa có project" />
                  ) : (
                    <Row gutter={[16, 16]}>
                      {projects.data?.items.map((p) => (
                        <Col key={p.id} xs={24} sm={12} xl={8}>
                          <ToolCard p={p} />
                        </Col>
                      ))}
                    </Row>
                  ),
              },
              {
                key: 'members',
                label: `Thành viên (${g.member_count})`,
                children: (
                  <Card
                    size="small"
                    extra={
                      manage && (
                        <Button icon={<UserAddOutlined />} onClick={() => setAdding(true)}>
                          Thêm thành viên
                        </Button>
                      )
                    }
                  >
                    <Table<GroupMember>
                      rowKey={(m) => m.user.id}
                      loading={members.isLoading}
                      dataSource={members.data}
                      pagination={false}
                      scroll={{ x: 600 }}
                      columns={[
                        {
                          title: 'Người dùng',
                          render: (_, m) => (
                            <Space size={6}>
                              <b>{m.user.full_name ?? m.user.username}</b>
                              <Typography.Text type="secondary">@{m.user.username}</Typography.Text>
                            </Space>
                          ),
                        },
                        {
                          title: 'Role',
                          width: 180,
                          render: (_, m) =>
                            manage ? (
                              <Select
                                size="small"
                                value={m.role}
                                options={ROLE_OPTIONS}
                                style={{ width: 150 }}
                                onChange={(role) => changeRole.mutate({ userId: m.user.id, role })}
                              />
                            ) : (
                              <RoleTag role={m.role} />
                            ),
                        },
                        { title: 'Tham gia', render: (_, m) => <TimeAgo value={m.created_at} /> },
                        {
                          title: '',
                          width: 90,
                          render: (_, m) =>
                            manage && (
                              <Popconfirm title={`Xoá ${m.user.username} khỏi group?`} onConfirm={() => remove.mutate(m.user.id)}>
                                <Button size="small" danger>
                                  Xoá
                                </Button>
                              </Popconfirm>
                            ),
                        },
                      ]}
                    />
                  </Card>
                ),
              },
            ]}
          />
          <Modal title="Thêm thành viên group" open={adding} onCancel={() => setAdding(false)} footer={null} destroyOnClose>
            {add.error && <Alert type="error" message={errorMessage(add.error)} style={{ marginBottom: 12 }} />}
            <Form layout="vertical" initialValues={{ role: 'DEVELOPER' }} onFinish={(v) => add.mutate(v)}>
              <Form.Item name="user_id" label="Người dùng" rules={[{ required: true }]}>
                <UserPicker />
              </Form.Item>
              <Form.Item name="role" label="Role">
                <Select options={ROLE_OPTIONS} />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={add.isPending}>
                Thêm
              </Button>
            </Form>
          </Modal>
          <CreateProjectModal open={creating} onClose={() => setCreating(false)} defaultGroup={slug} />
        </>
      )}
    </Skeleton>
  )
}
