import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Alert, Button, Card, Input, Modal, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { DownloadOutlined, StopOutlined, UndoOutlined } from '@ant-design/icons'
import { api, errorMessage } from '@/api/client'
import type { PackageFile, PackageVersion, Project } from '@/api/types'
import { CopyCommand, EmptyState, ShortSha, TimeAgo, formatBytes } from '@/components/common'

export default function VersionsTab({ project: p }: { project: Project }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const canYank = p.permissions.includes('package.yank')
  const [yanking, setYanking] = useState<PackageVersion | null>(null)
  const [reason, setReason] = useState('')
  const versions = useQuery({
    queryKey: ['versions', p.full_path],
    queryFn: () => api.get<PackageVersion[]>(`projects/${p.full_path}/packages`),
  })
  const yank = useMutation({
    mutationFn: ({ v, yank, reason }: { v: PackageVersion; yank: boolean; reason: string }) =>
      api.post(`packages/${v.normalized_name}/${v.version}/yank`, { reason, yank }),
    onSuccess: (_, vars) => {
      message.success(vars.yank ? `Đã yank ${vars.v.version}` : `Đã bỏ yank ${vars.v.version}`)
      setYanking(null)
      setReason('')
      qc.invalidateQueries({ queryKey: ['versions', p.full_path] })
    },
    onError: (e) => message.error(errorMessage(e)),
  })

  if (versions.error) return <Alert type="error" message={errorMessage(versions.error)} />
  if (versions.data && versions.data.length === 0) {
    return (
      <EmptyState title="Chưa có version nào được publish">
        <Typography.Text type="secondary">Tạo tag khớp version trong pyproject.toml rồi push:</Typography.Text>
        <CopyCommand value="git tag v0.1.0 && git push origin v0.1.0" />
      </EmptyState>
    )
  }

  return (
    <>
      <Table<PackageVersion>
        rowKey="id"
        loading={versions.isLoading}
        dataSource={versions.data}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        scroll={{ x: 760 }}
        expandable={{
          expandedRowRender: (v) => (
            <Card size="small" title="File & SHA256">
              <Table<PackageFile>
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={v.files}
                scroll={{ x: 700 }}
                columns={[
                  { title: 'File', dataIndex: 'filename', render: (f) => <code>{f}</code> },
                  { title: 'Loại', dataIndex: 'file_type', width: 80 },
                  { title: 'Kích thước', width: 100, render: (_, f) => formatBytes(f.size_bytes) },
                  {
                    title: 'SHA256',
                    render: (_, f) => (
                      <Typography.Text code copyable={{ text: f.sha256 }} style={{ fontSize: 11 }}>
                        {f.sha256.slice(0, 16)}…
                      </Typography.Text>
                    ),
                  },
                  {
                    title: '',
                    width: 60,
                    render: (_, f) => (
                      <Tooltip title="Tải file">
                        <Button size="small" icon={<DownloadOutlined />} href={f.download_url} />
                      </Tooltip>
                    ),
                  },
                ]}
              />
              {((v.metadata_json?.requires_dist as string[] | undefined) ?? []).length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <Typography.Text strong>Dependencies: </Typography.Text>
                  {(v.metadata_json.requires_dist as string[]).map((d) => (
                    <Tag key={d}>{d}</Tag>
                  ))}
                </div>
              )}
            </Card>
          ),
        }}
        columns={[
          {
            title: 'Version',
            render: (_, v) => (
              <Space>
                <Typography.Text strong delete={v.is_yanked}>
                  {v.version}
                </Typography.Text>
                {v.is_yanked && (
                  <Tooltip title={v.yanked_reason}>
                    <Tag color="red">yanked</Tag>
                  </Tooltip>
                )}
                {v.version === p.latest_release_version && !v.is_yanked && <Tag color="green">latest</Tag>}
              </Space>
            ),
          },
          { title: 'Git tag', dataIndex: 'git_tag', render: (t) => <Tag color="gold">{t}</Tag> },
          { title: 'Commit', render: (_, v) => <ShortSha sha={v.commit_sha} /> },
          {
            title: 'Build',
            render: (_, v) => (v.build_id ? <Link to={`/builds/${v.build_id}`}>#{v.build_number}</Link> : '—'),
          },
          { title: 'Publish', render: (_, v) => <TimeAgo value={v.created_at} /> },
          { title: 'Lượt tải', dataIndex: 'download_count', width: 90 },
          {
            title: '',
            width: 200,
            render: (_, v) => (
              <Space>
                <CopyCommand value={`pip install ${v.normalized_name}==${v.version}`} />
                {canYank &&
                  (v.is_yanked ? (
                    <Tooltip title="Bỏ yank">
                      <Button size="small" icon={<UndoOutlined />} onClick={() => yank.mutate({ v, yank: false, reason: 'unyank' })} />
                    </Tooltip>
                  ) : (
                    <Tooltip title="Yank (ẩn khỏi resolve, không xoá)">
                      <Button size="small" danger icon={<StopOutlined />} onClick={() => setYanking(v)} />
                    </Tooltip>
                  ))}
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title={`Yank ${yanking?.normalized_name}==${yanking?.version}`}
        open={!!yanking}
        onCancel={() => setYanking(null)}
        okText="Yank"
        okButtonProps={{ danger: true, disabled: !reason.trim(), loading: yank.isPending }}
        onOk={() => yanking && yank.mutate({ v: yanking, yank: true, reason })}
      >
        <Typography.Paragraph>
          Version bị yank sẽ không được pip chọn khi resolve dải version, nhưng <b>vẫn cài được</b> khi pin chính xác
          (<code>==</code>). Package không bao giờ bị xoá cứng.
        </Typography.Paragraph>
        <Input.TextArea rows={3} placeholder="Lý do (bắt buộc)" value={reason} onChange={(e) => setReason(e.target.value)} />
      </Modal>
    </>
  )
}
