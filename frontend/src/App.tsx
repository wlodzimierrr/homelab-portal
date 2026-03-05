import { useCallback, useEffect, useMemo, useState } from 'react'
import { ToastMessage } from '@/components/toast-message'
import { UNAUTHORIZED_EVENT } from '@/lib/api'
import { getPlatformIncidentFeed } from '@/lib/adapters/platform-health'
import { useAuth } from '@/lib/auth'
import { config } from '@/lib/config'
import {
  buildIncidentAlertSnapshot,
  normalizeIncidentSeverityThreshold,
  shouldShowIncidentBanner,
  type IncidentAlertSnapshot,
} from '@/lib/incident-alerts'
import { LoginPage } from '@/pages/login-page'
import { DashboardPage } from '@/pages/dashboard-page'
import { PlatformHealthPage } from '@/pages/platform-health-page'
import { ProjectsPage } from '@/pages/projects-page'
import { ServiceDeploymentsPage } from '@/pages/service-deployments-page'
import { ServiceDetailsPage } from '@/pages/service-details-page'
import { ServicesPage } from '@/pages/services-page'
import { SettingsPage } from '@/pages/settings-page'
import { PortalLayout } from '@/components/layout/portal-layout'
import { getServiceIdFromPath, isServiceDeploymentsPath, isServiceDetailsPath } from '@/lib/routes'

type Theme = 'light' | 'dark'
const INCIDENT_BANNER_DISMISSED_KEY = 'portal-incident-banner-dismissed'
const INCIDENT_POLL_INTERVAL_MS = 60_000
const EMPTY_INCIDENT_SNAPSHOT: IncidentAlertSnapshot = {
  activeCount: 0,
  highestSeverity: null,
  serviceAlerts: {},
}

function App() {
  const { token, clearToken } = useAuth()
  const [pathname, setPathname] = useState(window.location.pathname)
  const [toastMessage, setToastMessage] = useState('')
  const [incidentSnapshot, setIncidentSnapshot] = useState<IncidentAlertSnapshot>(EMPTY_INCIDENT_SNAPSHOT)
  const [isIncidentBannerDismissed, setIsIncidentBannerDismissed] = useState(() => {
    return window.sessionStorage.getItem(INCIDENT_BANNER_DISMISSED_KEY) === '1'
  })
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

  useEffect(() => {
    if (!token) {
      return
    }

    let cancelled = false

    const loadIncidents = async () => {
      try {
        const feed = await getPlatformIncidentFeed()
        if (cancelled) {
          return
        }
        setIncidentSnapshot(buildIncidentAlertSnapshot(feed.incidents))
      } catch {
        if (cancelled) {
          return
        }
        setIncidentSnapshot(EMPTY_INCIDENT_SNAPSHOT)
      }
    }

    void loadIncidents()
    const intervalId = window.setInterval(() => void loadIncidents(), INCIDENT_POLL_INTERVAL_MS)

    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [token])

  const dismissIncidentBanner = useCallback(() => {
    setIsIncidentBannerDismissed(true)
    window.sessionStorage.setItem(INCIDENT_BANNER_DISMISSED_KEY, '1')
  }, [])

  const serviceId = useMemo(() => getServiceIdFromPath(pathname), [pathname])
  const incidentThreshold = useMemo(
    () => normalizeIncidentSeverityThreshold(config.incidentBannerMinSeverity),
    [],
  )
  const showIncidentBanner = useMemo(
    () =>
      pathname !== '/login' &&
      shouldShowIncidentBanner(incidentSnapshot, {
        threshold: incidentThreshold,
        dismissed: isIncidentBannerDismissed,
      }),
    [incidentSnapshot, incidentThreshold, isIncidentBannerDismissed, pathname],
  )
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
      return <ServicesPage incidentServiceAlerts={incidentSnapshot.serviceAlerts} />
    }
    if (pathname === '/platform-health') {
      return <PlatformHealthPage />
    }
    if (isServiceDeploymentsPath(pathname)) {
      return <ServiceDeploymentsPage serviceId={serviceId} />
    }
    if (isServiceDetailsPath(pathname)) {
      return <ServiceDetailsPage serviceId={serviceId} incidentServiceAlerts={incidentSnapshot.serviceAlerts} />
    }
    if (pathname === '/settings') {
      return <SettingsPage />
    }
    return <DashboardPage />
  }, [handleLoginSuccess, incidentSnapshot.serviceAlerts, pathname, serviceId])

  if (pathname === '/login') {
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
        onThemeToggle={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
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

export default App
