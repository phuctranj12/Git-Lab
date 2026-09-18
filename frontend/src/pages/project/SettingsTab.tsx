import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Form, Input, Popconfirm, Radio, Space, Switch, Typography } from 'antd'
import { api, errorMessage } from '@/api/client'
import type { Project } from '@/api/types'

export default function SettingsTab({ project: p }: { project: Project }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const refresh = () => qc.invalidateQueries({ queryKey: ['project', p.group_slug, p.slug] })

  const save = useMutation({
    mutationFn: (v: Partial<Project>) => api.patch<Project>(`projects/${p.full_path}`, v),
    onSuccess: () => {
      message.success('Đã lưu')
      refresh()
    },
  })
  const archive = useMutation({
    mutationFn: (on: boolean) => api.post(`projects/${p.full_path}/${on ? 'archive' : 'unarchive'}`),
    onSuccess: () => refresh(),
    onError: (e) => message.error(errorMessage(e)),
  })
  const del = useMutation({
    mutationFn: () => api.del(`projects/${p.full_path}`),
    onSuccess: () => {
      message.success('Đã xoá project')
      navigate('/tools')
    },
    onError: (e) => message.error(errorMessage(e)),
  })

  return (
    <Space direction="vertical" size={16} style={{ width: '100%', maxWidth: 760 }}>
      <Card title="Thông tin chung">
        {save.error && <Alert type="error" message={errorMessage(save.error)} style={{ marginBottom: 12 }} />}
        <Form
          layout="vertical"
          disabled={p.archived}
          initialValues={{
            name: p.name,
            description: p.description,
            visibility: p.visibility,
            default_branch: p.default_branch,
            protect_default_branch: p.protect_default_branch,
          }}
          onFinish={(v) => save.mutate(v)}
        >
          <Form.Item name="name" label="Tên" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="Mô tả">
            <Input.TextArea rows={3} />
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
          <Form.Item name="default_branch" label="Nhánh mặc định">
            <Input style={{ maxWidth: 220 }} />
          </Form.Item>
          <Form.Item
            name="protect_default_branch"
            label="Bảo vệ nhánh mặc định"
            valuePropName="checked"
            extra="Bật: chỉ Maintainer trở lên được push trực tiếp lên nhánh mặc định; Developer push nhánh khác."
          >
            <Switch />
          </Form.Item>
          <Typography.Paragraph type="secondary">
            Tên package <code>{p.package_name ?? '—'}</code> và slug không đổi được sau khi tạo (tránh gãy pip/Git URL).
          </Typography.Paragraph>
          <Button type="primary" htmlType="submit" loading={save.isPending}>
            Lưu thay đổi
          </Button>
        </Form>
      </Card>
      {p.permissions.includes('project.archive') && (
        <Card title="Vùng nguy hiểm" styles={{ header: { color: '#cf1322' } }}>
          <Space direction="vertical">
            {p.archived ? (
              <Button onClick={() => archive.mutate(false)} loading={archive.isPending}>
                Bỏ archive
              </Button>
            ) : (
              <Popconfirm
                title="Archive project?"
                description="Repository thành chỉ đọc, không build mới. Package đã publish vẫn cài được."
                onConfirm={() => archive.mutate(true)}
              >
                <Button danger loading={archive.isPending}>
                  Archive project
                </Button>
              </Popconfirm>
            )}
            {p.permissions.includes('project.delete') && p.archived && (
              <Popconfirm
                title="Xoá hẳn project?"
                description="Chỉ xoá được project chưa từng publish package. Repository được chuyển vào thùng rác trên server."
                onConfirm={() => del.mutate()}
              >
                <Button danger type="primary" loading={del.isPending}>
                  Xoá project (System Admin)
                </Button>
              </Popconfirm>
            )}
          </Space>
        </Card>
      )}
    </Space>
  )
}
