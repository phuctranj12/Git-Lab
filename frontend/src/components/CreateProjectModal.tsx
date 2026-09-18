import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Form, Input, Modal, Radio, Select } from 'antd'
import { api, errorMessage } from '@/api/client'
import type { Group, Project } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'

function slugify(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 100)
}

interface Props {
  open: boolean
  onClose: () => void
  defaultGroup?: string
}

export default function CreateProjectModal({ open, onClose, defaultGroup }: Props) {
  const { meta } = useAuth()
  const prefix = meta?.internal_package_prefix ?? 'hawee-'
  const [form] = Form.useForm()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [slugTouched, setSlugTouched] = useState(false)
  const [pkgTouched, setPkgTouched] = useState(false)
  const groups = useQuery({ queryKey: ['groups', 'all'], queryFn: () => api.get<Group[]>('groups'), enabled: open })
  const manageable = (groups.data ?? []).filter((g) => g.can_manage)
  const language = Form.useWatch('language', form)

  useEffect(() => {
    if (open) {
      form.resetFields()
      setSlugTouched(false)
      setPkgTouched(false)
      if (defaultGroup) form.setFieldValue('group', defaultGroup)
    }
  }, [open, defaultGroup, form])

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post<Project>('projects', body),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      onClose()
      navigate(`/p/${p.full_path}`)
    },
  })

  return (
    <Modal
      title="Tạo tool / project mới"
      open={open}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="Tạo project"
      confirmLoading={create.isPending}
      destroyOnClose
      width={560}
    >
      {create.error && <Alert type="error" showIcon message={errorMessage(create.error)} style={{ marginBottom: 12 }} />}
      {groups.data && manageable.length === 0 && (
        <Alert type="info" showIcon message="Bạn cần là Owner của một group (hoặc System Admin) để tạo project." style={{ marginBottom: 12 }} />
      )}
      <Form
        form={form}
        layout="vertical"
        initialValues={{ language: 'PYTHON', visibility: 'INTERNAL', default_branch: 'main' }}
        onValuesChange={(changed) => {
          if ('name' in changed) {
            const slug = slugify(changed.name ?? '')
            if (!slugTouched) form.setFieldValue('slug', slug)
            if (!pkgTouched) form.setFieldValue('package_name', slug ? `${prefix}${slug}` : '')
          }
          if ('slug' in changed) {
            setSlugTouched(true)
            if (!pkgTouched) form.setFieldValue('package_name', changed.slug ? `${prefix}${changed.slug}` : '')
          }
          if ('package_name' in changed) setPkgTouched(true)
        }}
        onFinish={(v) => create.mutate({ ...v, package_name: v.language === 'PYTHON' ? v.package_name : undefined })}
      >
        <Form.Item name="group" label="Group" rules={[{ required: true, message: 'Chọn group' }]}>
          <Select
            loading={groups.isLoading}
            options={manageable.map((g) => ({ value: g.slug, label: `${g.name} (${g.slug})` }))}
            placeholder="Chọn group"
          />
        </Form.Item>
        <Form.Item name="name" label="Tên tool" rules={[{ required: true, message: 'Nhập tên' }]}>
          <Input placeholder="Bóc tách bản vẽ" />
        </Form.Item>
        <Form.Item
          name="slug"
          label="Slug (đường dẫn repository)"
          rules={[{ required: true }, { pattern: /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/, message: 'Chỉ a-z, 0-9 và dấu -' }]}
        >
          <Input placeholder="boc-tach-ban-ve" />
        </Form.Item>
        <Form.Item name="language" label="Loại project">
          <Radio.Group
            options={[
              { value: 'PYTHON', label: 'Python package' },
              { value: 'GENERIC', label: 'Chỉ Git (không publish)' },
            ]}
            optionType="button"
          />
        </Form.Item>
        {language === 'PYTHON' && (
          <Form.Item
            name="package_name"
            label="Tên package (pip install …)"
            extra={`Bắt buộc bắt đầu bằng "${prefix}" — chống dependency confusion. Phải trùng [project].name trong pyproject.toml.`}
            rules={[
              { required: true, message: 'Nhập tên package' },
              {
                validator: (_, v: string) =>
                  !v || v.toLowerCase().replace(/[-_.]+/g, '-').startsWith(prefix)
                    ? Promise.resolve()
                    : Promise.reject(new Error(`Phải bắt đầu bằng ${prefix}`)),
              },
            ]}
          >
            <Input placeholder={`${prefix}boc-tach-ban-ve`} />
          </Form.Item>
        )}
        <Form.Item name="description" label="Mô tả">
          <Input.TextArea rows={2} placeholder="Tool làm gì, ai dùng…" />
        </Form.Item>
        <Form.Item name="visibility" label="Phạm vi">
          <Radio.Group
            options={[
              { value: 'INTERNAL', label: 'Internal — mọi nhân viên đăng nhập đều xem/cài được' },
              { value: 'PRIVATE', label: 'Private — chỉ thành viên' },
            ]}
          />
        </Form.Item>
        <Form.Item name="default_branch" label="Nhánh mặc định">
          <Input style={{ maxWidth: 200 }} />
        </Form.Item>
      </Form>
    </Modal>
  )
}
