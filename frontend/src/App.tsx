import { useCallback, useEffect, useMemo, useState } from 'react'
import { Toast } from '@/components/ui/toast'
import { UNAUTHORIZED_EVENT } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { LoginPage } from '@/pages/login-page'
import { DashboardPage } from '@/pages/dashboard-page'
import { ProjectsPage } from '@/pages/projects-page'
import { ServiceDeploymentsPage } from '@/pages/service-deployments-page'
import { ServiceDetailsPage } from '@/pages/service-details-page'
import { ServicesPage } from '@/pages/services-page'
import { SettingsPage } from '@/pages/settings-page'
import { PortalLayout } from '@/components/layout/portal-layout'
import { getServiceIdFromPath, isServiceDeploymentsPath, isServiceDetailsPath } from '@/lib/routes'

type Theme = 'light' | 'dark'

function App() {
  const { token, clearToken } = useAuth()
  const [pathname, setPathname] = useState(window.location.pathname)
  const [toastMessage, setToastMessage] = useState('')
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = window.localStorage.getItem('portal-theme')
    return stored === 'dark' ? 'dark' : 'light'
  })

  const navigate = useCallback((path: string, replace = false) => {
    if (replace) {
      window.history.replaceState({}, '', path)
    } else {
      window.history.pushState({}, '', path)
    }
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [])

  useEffect(() => {
    const onPopState = () => setPathname(window.location.pathname)
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    window.localStorage.setItem('portal-theme', theme)
  }, [theme])

  useEffect(() => {
    if (pathname !== '/login' && !token) {
      navigate('/login', true)
      return
    }

    if (pathname === '/login' && token) {
      navigate('/dashboard', true)
    }
  }, [navigate, pathname, token])

  useEffect(() => {
    const handleUnauthorized = (event: Event) => {
      clearToken()
      const message =
        event instanceof CustomEvent && typeof event.detail?.message === 'string'
          ? event.detail.message
          : 'Unauthorized request. Please sign in again.'

      setToastMessage(message)
      navigate('/login', true)
    }

    window.addEventListener(UNAUTHORIZED_EVENT, handleUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handleUnauthorized)
  }, [clearToken, navigate])

  useEffect(() => {
    if (!toastMessage) {
      return
    }

    const timer = window.setTimeout(() => setToastMessage(''), 4500)
    return () => window.clearTimeout(timer)
  }, [toastMessage])

  const serviceId = useMemo(() => getServiceIdFromPath(pathname), [pathname])
  const handleLoginSuccess = useCallback(() => navigate('/dashboard', true), [navigate])

  const content = useMemo(() => {
    if (pathname === '/login') {
      return <LoginPage onLoginSuccess={handleLoginSuccess} />
    }
    if (pathname === '/dashboard' || pathname === '/') {
      return <DashboardPage />
    }
    if (pathname === '/projects') {
      return <ProjectsPage />
    }
    if (pathname === '/services') {
      return <ServicesPage />
    }
    if (isServiceDeploymentsPath(pathname)) {
      return <ServiceDeploymentsPage serviceId={serviceId} />
    }
    if (isServiceDetailsPath(pathname)) {
      return <ServiceDetailsPage serviceId={serviceId} />
    }
    if (pathname === '/settings') {
      return <SettingsPage />
    }
    return <DashboardPage />
  }, [handleLoginSuccess, pathname, serviceId])

  if (pathname === '/login') {
    return (
      <>
        {content}
        {toastMessage ? <Toast message={toastMessage} onClose={() => setToastMessage('')} /> : null}
      </>
    )
  }

  return (
    <>
      <PortalLayout pathname={pathname} theme={theme} onThemeToggle={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
        {content}
      </PortalLayout>
      {toastMessage ? <Toast message={toastMessage} onClose={() => setToastMessage('')} /> : null}
    </>
  )
}

export default App
