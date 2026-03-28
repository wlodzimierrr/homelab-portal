import type { AppRoute } from '@/app/router'
import { ServiceAreaRouteContent } from '@/features/service-area/route-content'
import type { ServiceIncidentBadge } from '@/lib/incident-alerts'
import { DashboardPage } from '@/pages/dashboard-page'
import { LoginPage } from '@/pages/login-page'
import { PlatformHealthPage } from '@/pages/platform-health-page'
import { ProjectsPage } from '@/pages/projects-page'
import { ServicesPage } from '@/pages/services-page'
import { SettingsPage } from '@/pages/settings-page'

interface AppRouteContentProps {
  route: AppRoute
  incidentServiceAlerts: Record<string, ServiceIncidentBadge>
  onLoginSuccess: () => void
}

// Route-to-page composition lives outside AppShell so the shell only manages
// auth, navigation, and layout concerns rather than page ownership details.
export function AppRouteContent({
  route,
  incidentServiceAlerts,
  onLoginSuccess,
}: AppRouteContentProps) {
  switch (route.kind) {
    case 'login':
      return <LoginPage onLoginSuccess={onLoginSuccess} />
    case 'dashboard':
      return <DashboardPage />
    case 'projects':
      return <ProjectsPage />
    case 'services':
      return <ServicesPage incidentServiceAlerts={incidentServiceAlerts} />
    case 'platform-health':
      return <PlatformHealthPage />
    case 'service-settings':
    case 'service-overview':
    case 'service-deployments':
      return (
        <ServiceAreaRouteContent
          route={route}
          incidentServiceAlerts={incidentServiceAlerts}
        />
      )
    case 'settings':
      return <SettingsPage />
    default:
      return <DashboardPage />
  }
}
