import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Input, Modal, Radio, Row, Skeleton, Space, Typography } from 'antd'
import { PlusOutlined, TeamOutlined } from '@ant-design/icons'
import { api, errorMessage } from '@/api/client'
import type { Group } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { EmptyState, PageHeader, RoleTag, VisibilityTag } from '@/components/common'
import UserPicker from '@/components/UserPicker'

export default function GroupsPage() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const groups = useQuery({ queryKey: ['groups', 'all'], queryFn: () => api.get<Group[]>('groups') })
  const create = useMutation({
    mutationFn: (v: Record<string, unknown>) => api.post<Group>('groups', v),
    onSuccess: () => {
      setOpen(false)
      qc.invalidateQueries({ queryKey: ['groups'] })
    },
  })
  return (
    <>
      <PageHeader
        title="Group"
        subtitle="Nhóm/team sở hữu tool. Quyền trong group áp cho mọi project của group."
        extra={
          user?.is_system_admin && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
              Tạo group
            </Button>
          )
        }
      />
      {groups.error && <Alert type="error" message={errorMessage(groups.error)} />}
      <Skeleton loading={groups.isLoading} active>
        {groups.data?.length === 0 ? (
          <EmptyState title="Chưa có group nào" />
        ) : (
          <Row gutter={[16, 16]}>
            {groups.data?.map((g) => (
              <Col key={g.id} xs={24} sm={12} xl={8}>
                <Link to={`/groups/${g.slug}`}>
                  <Card className="tool-card" size="small" hoverable>
                    <Space style={{ justifyContent: 'space-between', width: '100%' }}>
                      <Space>
                        <TeamOutlined style={{ fontSize: 20, color: '#1d5fa8' }} />
                        <Typography.Title level={5} style={{ margin: 0 }}>
                          {g.name}
                        </Typography.Title>
                      </Space>
                      <VisibilityTag value={g.visibility} />
                    </Space>
                    <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ margin: 0 }}>
                      {g.description || g.slug}
                    </Typography.Paragraph>
                    <Space>
                      <Typography.Text type="secondary">
                        {g.project_count} project · {g.member_count} thành viên
                      </Typography.Text>
                      {g.my_role && <RoleTag role={g.my_role} />}
                    </Space>
                  </Card>
                </Link>
              </Col>
            ))}
          </Row>
        )}
      </Skeleton>
      <Modal title="Tạo group" open={open} onCancel={() => setOpen(false)} footer={null} destroyOnClose>
        {create.error && <Alert type="error" message={errorMessage(create.error)} style={{ marginBottom: 12 }} />}
        <Form layout="vertical" initialValues={{ visibility: 'INTERNAL' }} onFinish={(v) => create.mutate(v)}>
          <Form.Item name="name" label="Tên" rules={[{ required: true }]}>
            <Input placeholder="AI Team" />
          </Form.Item>
          <Form.Item
            name="slug"
            label="Slug"
            rules={[{ required: true }, { pattern: /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/, message: 'a-z, 0-9, -' }]}
          >
            <Input placeholder="ai-tools" />
          </Form.Item>
          <Form.Item name="description" label="Mô tả">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="visibility" label="Phạm vi">
            <Radio.Group
              options={[
                { value: 'INTERNAL', label: 'Internal' },
                { value: 'PRIVATE', label: 'Private' },
              ]}
              optionType="button"
            />
          </Form.Item>
          <Form.Item name="owner_id" label="Owner ban đầu" extra="Bỏ trống = bạn là Owner">
            <UserPicker />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={create.isPending}>
            Tạo group
          </Button>
        </Form>
      </Modal>
    </>
  )
}
