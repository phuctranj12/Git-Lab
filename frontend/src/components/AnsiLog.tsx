import { useEffect, useMemo, useRef, type CSSProperties, type ReactNode } from 'react'

const COLORS: Record<number, string> = {
  30: '#5c6370', 31: '#ff6b6b', 32: '#8fd18f', 33: '#f5c26b', 34: '#6cb6ff', 35: '#d38aea', 36: '#56d4dd', 37: '#e6e6e6',
  90: '#7f8894', 91: '#ff8787', 92: '#a9e5a9', 93: '#ffd88a', 94: '#8cc8ff', 95: '#e0a6f0', 96: '#7fe3ea', 97: '#ffffff',
}

// Chuyển mã màu ANSI (SGR) thành span — không dùng dangerouslySetInnerHTML.
function render(text: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /\x1b\[([0-9;]*)m/g
  let style: CSSProperties = {}
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  const push = (chunk: string) => {
    if (chunk) out.push(<span key={i++} style={style}>{chunk}</span>)
  }
  while ((m = re.exec(text))) {
    push(text.slice(last, m.index))
    last = re.lastIndex
    const codes = m[1] ? m[1].split(';').map(Number) : [0]
    style = { ...style }
    for (const c of codes) {
      if (c === 0) style = {}
      else if (c === 1) style.fontWeight = 700
      else if (c === 22) delete style.fontWeight
      else if (COLORS[c]) style.color = COLORS[c]
      else if (c === 39) delete style.color
    }
  }
  push(text.slice(last))
  return out
}

export default function AnsiLog({ text, follow }: { text: string; follow?: boolean }) {
  const ref = useRef<HTMLPreElement>(null)
  const nodes = useMemo(() => render(text.replace(/\x1b\[[0-9;]*[A-Za-z]/g, (s) => (s.endsWith('m') ? s : ''))), [text])
  useEffect(() => {
    const el = ref.current
    if (follow && el) el.scrollTop = el.scrollHeight
  }, [nodes, follow])
  return (
    <pre ref={ref} className="build-log">
      {text ? nodes : <span style={{ color: '#7f8894' }}>(chưa có log)</span>}
    </pre>
  )
}
