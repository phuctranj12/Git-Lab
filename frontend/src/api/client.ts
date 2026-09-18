// Gọi API: token nằm trong cookie HttpOnly (FE không cầm token), CSRF double-submit qua header,
// 401 → tự gọi /auth/refresh MỘT lần (gộp các request đồng thời) rồi thử lại.

export class ApiError extends Error {
  status: number
  code: string
  details?: unknown
  requestId?: string

  constructor(status: number, code: string, message: string, details?: unknown, requestId?: string) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
    this.requestId = requestId
  }
}

type Query = Record<string, string | number | boolean | undefined | null>

export interface RequestOptions {
  method?: string
  body?: unknown
  query?: Query
  form?: FormData
  signal?: AbortSignal
}

const SAFE = new Set(['GET', 'HEAD', 'OPTIONS'])

function readCookie(name: string): string | undefined {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return m ? decodeURIComponent(m[1]) : undefined
}

export function buildUrl(path: string, query?: Query): string {
  const url = path.startsWith('/') ? path : `/api/v1/${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

let refreshPromise: Promise<boolean> | null = null

async function refreshSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'include' })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        setTimeout(() => (refreshPromise = null), 0)
      })
  }
  return refreshPromise
}

async function doFetch(path: string, opts: RequestOptions): Promise<Response> {
  const method = (opts.method ?? (opts.body || opts.form ? 'POST' : 'GET')).toUpperCase()
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (!SAFE.has(method)) {
    const token = readCookie('toolhub_csrf')
    if (token) headers['X-CSRF-Token'] = token
  }
  let body: BodyInit | undefined
  if (opts.form) body = opts.form
  else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(opts.body)
  }
  return fetch(buildUrl(path, opts.query), { method, headers, body, credentials: 'include', signal: opts.signal })
}

async function toError(resp: Response): Promise<ApiError> {
  let payload: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } = {}
  try {
    payload = await resp.json()
  } catch {
    /* body không phải JSON */
  }
  const e = payload.error ?? {}
  return new ApiError(resp.status, e.code ?? 'HTTP_ERROR', e.message ?? `HTTP ${resp.status}`, e.details, e.request_id)
}

export async function request<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  let resp = await doFetch(path, opts)
  const isAuthCall = path.includes('/auth/')
  if (resp.status === 401 && !isAuthCall) {
    if (await refreshSession()) resp = await doFetch(path, opts)
    if (resp.status === 401) window.dispatchEvent(new Event('auth:expired'))
  }
  if (!resp.ok) throw await toError(resp)
  if (resp.status === 204) return undefined as T
  const ctype = resp.headers.get('content-type') ?? ''
  return (ctype.includes('json') ? resp.json() : resp.text()) as Promise<T>
}

export const api = {
  get: <T,>(path: string, query?: Query) => request<T>(path, { query }),
  post: <T,>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body: body ?? {} }),
  patch: <T,>(path: string, body: unknown) => request<T>(path, { method: 'PATCH', body }),
  del: <T,>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'VALIDATION_ERROR' && err.details && typeof err.details === 'object') {
      const errors = (err.details as { errors?: { loc: string[]; msg: string }[] }).errors
      if (errors?.length) return errors.map((e) => `${e.loc.slice(1).join('.')}: ${e.msg}`).join('; ')
    }
    return err.message
  }
  return err instanceof Error ? err.message : String(err)
}
