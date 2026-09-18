import { Link } from 'react-router-dom'
import { Space, Table, Tag } from 'antd'
import type { Build } from '@/api/types'
import { BuildStatusTag, ShortSha, TimeAgo, formatDuration } from '@/components/common'

export function BuildTable({ builds, showProject = true, compact }: { builds: Build[]; showProject?: boolean; compact?: boolean }) {
  return (
    <Table<Build>
      size={compact ? 'small' : 'middle'}
      rowKey="id"
      dataSource={builds}
      pagination={false}
      scroll={{ x: 640 }}
      columns={[
        {
          title: 'Build',
          render: (_, b) => (
            <Link to={`/builds/${b.id}`}>
              <b>#{b.number}</b>
            </Link>
          ),
          width: 70,
        },
        ...(showProject
          ? [{ title: 'Project', ellipsis: true, render: (_: unknown, b: Build) => <Link to={`/p/${b.project_path}`} title={b.project_path ?? ''}>{b.project_path}</Link> }]
          : []),
        {
          title: 'Ref',
          render: (_, b) => (
            <Space size={4}>
              <Tag color={b.trigger_type === 'TAG' ? 'gold' : b.trigger_type === 'MANUAL' ? 'purple' : 'blue'}>
                {b.trigger_type === 'TAG' ? 'tag' : b.trigger_type === 'MANUAL' ? 'manual' : 'push'}
              </Tag>
              <span style={{ whiteSpace: 'nowrap' }}>{b.ref_name}</span>
            </Space>
          ),
        },
        { title: 'Trạng thái', render: (_, b) => <BuildStatusTag status={b.status} />, width: 130 },
        { title: 'Commit', render: (_, b) => <ShortSha sha={b.commit_sha} />, width: 110 },
        { title: 'Thời gian', render: (_, b) => formatDuration(b.duration_seconds), width: 90 },
        { title: 'Tạo', render: (_, b) => <TimeAgo value={b.created_at} />, width: 120 },
      ]}
    />
  )
}
