import { Link } from 'react-router-dom'
import { Card, Col, Row, Space, Typography } from 'antd'
import type { Project } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { CopyCommand } from '@/components/common'
import ServiceTokens from './ServiceTokens'

export default function PackagesTab({ project: p }: { project: Project }) {
  const { meta } = useAuth()
  const index = meta?.package_index_url ?? 'https://packages.hawee.local/simple'
  const host = index.replace(/^https?:\/\//, '').split('/')[0]
  const pin = p.latest_release_version ? `==${p.latest_release_version}` : ''
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={14}>
        <Card title="Cài package này">
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <Typography.Text>
              1. Cấu hình pip <b>một lần</b> — cùng một index cho cả package nội bộ lẫn PyPI (numpy, pandas…):
            </Typography.Text>
            <CopyCommand value={`pip config set global.index-url ${index}`} />
            <Typography.Text>
              2. Lưu token cá nhân (scope <code>read_package</code>, tạo ở <Link to="/settings">SSH key & token</Link>) vào{' '}
              <code>~/.netrc</code> (Windows: <code>%USERPROFILE%\_netrc</code>) — không ghi token vào requirements.txt:
            </Typography.Text>
            <CopyCommand value={`machine ${host} login __token__ password <TOKEN_CỦA_BẠN>`} />
            <Typography.Text>3. Cài:</Typography.Text>
            <CopyCommand value={`pip install ${p.package_name}${pin}`} />
            <Typography.Text type="secondary">requirements.txt chỉ ghi tên package, không ghi URL:</Typography.Text>
            <pre className="build-log" style={{ background: '#f6f8fa', color: '#24292f' }}>
{`numpy==2.1.0
pandas==2.2.3
${p.package_name}${pin || '==<version>'}`}
            </pre>
          </Space>
        </Card>
      </Col>
      <Col xs={24} lg={10}>
        <Card title="CI / server khác">
          <Typography.Paragraph type="secondary">
            Dùng <b>service token</b> gắn với project (chỉ đọc package). Token chỉ hiện một lần khi tạo.
          </Typography.Paragraph>
          <CopyCommand value={`pip install --index-url ${index.replace('://', '://__token__:$TOOLHUB_TOKEN@')} ${p.package_name}${pin}`} />
          {p.permissions.includes('project.tokens') && (
            <div style={{ marginTop: 16 }}>
              <ServiceTokens project={p} />
            </div>
          )}
        </Card>
      </Col>
    </Row>
  )
}
