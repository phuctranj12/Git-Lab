import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// react-markdown mặc định KHÔNG render raw HTML → an toàn XSS với README do user viết.
export default function Markdown({ source }: { source: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" /> }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
