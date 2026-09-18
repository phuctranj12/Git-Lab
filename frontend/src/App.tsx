import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Spin } from 'antd'
import { RequireAuth } from '@/auth/AuthContext'
import AppLayout from '@/layout/AppLayout'
import LoginPage from '@/pages/LoginPage'

const DashboardPage = lazy(() => import('@/pages/DashboardPage'))
const CataloguePage = lazy(() => import('@/pages/CataloguePage'))
const ProjectPage = lazy(() => import('@/pages/project/ProjectPage'))
const BuildsPage = lazy(() => import('@/pages/BuildsPage'))
const BuildDetailPage = lazy(() => import('@/pages/BuildDetailPage'))
const PackagesPage = lazy(() => import('@/pages/PackagesPage'))
const PackageRedirect = lazy(() => import('@/pages/PackageRedirect'))
const GroupsPage = lazy(() => import('@/pages/GroupsPage'))
const GroupPage = lazy(() => import('@/pages/GroupPage'))
const SettingsPage = lazy(() => import('@/pages/SettingsPage'))
const UsersPage = lazy(() => import('@/pages/admin/UsersPage'))
const AuditPage = lazy(() => import('@/pages/admin/AuditPage'))
const SystemPage = lazy(() => import('@/pages/admin/SystemPage'))

const fallback = (
  <div style={{ display: 'grid', placeItems: 'center', minHeight: 300 }}>
    <Spin />
  </div>
)

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Suspense fallback={fallback}><DashboardPage /></Suspense>} />
        <Route path="tools" element={<Suspense fallback={fallback}><CataloguePage /></Suspense>} />
        <Route path="p/:group/:project/:tab?" element={<Suspense fallback={fallback}><ProjectPage /></Suspense>} />
        <Route path="builds" element={<Suspense fallback={fallback}><BuildsPage /></Suspense>} />
        <Route path="builds/:id" element={<Suspense fallback={fallback}><BuildDetailPage /></Suspense>} />
        <Route path="packages" element={<Suspense fallback={fallback}><PackagesPage /></Suspense>} />
        <Route path="packages/:name" element={<Suspense fallback={fallback}><PackageRedirect /></Suspense>} />
        <Route path="groups" element={<Suspense fallback={fallback}><GroupsPage /></Suspense>} />
        <Route path="groups/:slug" element={<Suspense fallback={fallback}><GroupPage /></Suspense>} />
        <Route path="settings" element={<Suspense fallback={fallback}><SettingsPage /></Suspense>} />
        <Route path="admin/users" element={<RequireAuth admin><Suspense fallback={fallback}><UsersPage /></Suspense></RequireAuth>} />
        <Route path="admin/audit" element={<RequireAuth admin><Suspense fallback={fallback}><AuditPage /></Suspense></RequireAuth>} />
        <Route path="admin/system" element={<RequireAuth admin><Suspense fallback={fallback}><SystemPage /></Suspense></RequireAuth>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
