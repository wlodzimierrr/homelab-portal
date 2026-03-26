import { useCallback, useEffect, useMemo, useState } from 'react'
import { PortalLayout } from '@/components/layout/portal-layout'
import { ToastMessage } from '@/components/toast-message'
import { useAuth } from '@/lib/auth'
import { UNAUTHORIZED_EVENT } from '@/lib/http/auth-events'
import { DashboardPage } from '@/pages/dashboard-page'
import { LoginPage } from '@/pages/login-page'
import { PlatformHealthPage } from '@/pages/platform-health-page'
import { ProjectsPage } from '@/pages/projects-page'
import { ServiceDeploymentsPage } from '@/pages/service-deployments-page'
import { ServiceDetailsPage } from '@/pages/service-details-page'
import { ServicesPage } from '@/pages/services-page'
import { SettingsPage } from '@/pages/settings-page'
import { resolveAppRoute } from './router'
import { useIncidentFeed } from './use-incident-feed'
import { useTheme } from './use-theme'

function AppShell() {
  const { token, clearToken } = useAuth()
  const [pathname, setPathname] = useState(window.location.pathname)
  const [toastMessage, setToastMessage] = useState('')
  const { theme, toggleTheme } = useTheme()

  // The shell keeps lightweight browser-history routing so page navigation works
  // without introducing a router dependency mid-refactor.
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

  // Client-side auth guards keep the browser location aligned with session
  // state, while the backend remains the source of truth for authorization.
  useEffect(() => {
    if (pathname !== '/login' && !token) {
      navigate('/login', true)
      return
    }

    if (pathname === '/login' && token) {
      navigate('/dashboard', true)
    }
  }, [navigate, pathname, token])

  // Transport helpers emit one unauthorized event so the shell can clear local
  // auth state and redirect consistently instead of every page handling 401s.
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

  const route = useMemo(() => resolveAppRoute(pathname), [pathname])
  const { incidentSnapshot, showIncidentBanner, dismissIncidentBanner } = useIncidentFeed(token, pathname)
  const handleLoginSuccess = useCallback(() => navigate('/dashboard', true), [navigate])

  // Route rendering stays explicit even after the split so onboarding remains
  // straightforward and future routing changes have one obvious entrypoint.
  const content = useMemo(() => {
    switch (route.kind) {
      case 'login':
        return <LoginPage onLoginSuccess={handleLoginSuccess} />
      case 'dashboard':
        return <DashboardPage />
      case 'projects':
        return <ProjectsPage />
      case 'services':
        return <ServicesPage incidentServiceAlerts={incidentSnapshot.serviceAlerts} />
      case 'platform-health':
        return <PlatformHealthPage />
      case 'service-deployments':
        return <ServiceDeploymentsPage serviceId={route.serviceId} />
      case 'service-details':
        return (
          <ServiceDetailsPage
            serviceId={route.serviceId}
            incidentServiceAlerts={incidentSnapshot.serviceAlerts}
          />
        )
      case 'settings':
        return <SettingsPage />
      default:
        return <DashboardPage />
    }
  }, [handleLoginSuccess, incidentSnapshot.serviceAlerts, route])

  if (route.kind === 'login') {
    return (
      <>
        {content}
        {toastMessage ? <ToastMessage message={toastMessage} onClose={() => setToastMessage('')} /> : null}
      </>
    )
  }

  return (
    <>
      <PortalLayout
        pathname={pathname}
        theme={theme}
        onThemeToggle={toggleTheme}
        showIncidentBanner={showIncidentBanner}
        incidentActiveCount={incidentSnapshot.activeCount}
        incidentHighestSeverity={incidentSnapshot.highestSeverity}
        onIncidentDismiss={dismissIncidentBanner}
      >
        {content}
      </PortalLayout>
      {toastMessage ? <ToastMessage message={toastMessage} onClose={() => setToastMessage('')} /> : null}
    </>
  )
}

export default AppShell
