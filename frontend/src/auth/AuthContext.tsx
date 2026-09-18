import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Navigate, useLocation } from 'react-router-dom'
import { Spin } from 'antd'
import { api, ApiError } from '@/api/client'
import type { Meta, User } from '@/api/types'

interface AuthState {
  user: User | null
  meta: Meta | null
  loading: boolean
  login: (login: string, password: string) => Promise<User>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.get<Meta>('meta'), staleTime: Infinity })

  const refreshUser = useCallback(async () => {
    try {
      setUser(await api.get<User>('auth/me'))
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setUser(null)
      else throw e
    }
  }, [])

  useEffect(() => {
    refreshUser()
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
    const onExpired = () => {
      setUser(null)
      qc.clear()
    }
    window.addEventListener('auth:expired', onExpired)
    return () => window.removeEventListener('auth:expired', onExpired)
  }, [refreshUser, qc])

  const login = useCallback(async (loginName: string, password: string) => {
    const u = await api.post<User>('auth/login', { login: loginName, password })
    qc.clear()
    setUser(u)
    return u
  }, [qc])

  const logout = useCallback(async () => {
    try {
      await api.post('auth/logout')
    } finally {
      setUser(null)
      qc.clear()
    }
  }, [qc])

  const value = useMemo(
    () => ({ user, meta: meta.data ?? null, loading, login, logout, refreshUser }),
    [user, meta.data, loading, login, logout, refreshUser],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth ngoài AuthProvider')
  return ctx
}

export function RequireAuth({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) {
    return (
      <div style={{ display: 'grid', placeItems: 'center', height: '100vh' }}>
        <Spin size="large" />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  if (admin && !user.is_system_admin) return <Navigate to="/" replace />
  return <>{children}</>
}
