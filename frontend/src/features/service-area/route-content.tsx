import type { AppRoute } from '@/app/router'
import { ServiceDeploymentsPage } from '@/pages/service-deployments-page'
import { ServiceOverviewPage } from '@/pages/service-overview-page'
import { ServiceSettingsPage } from '@/pages/service-settings-page'
import type { ServiceIncidentBadge } from '@/lib/incident-alerts'

type ServiceAreaRoute = Extract<
  AppRoute,
  { kind: 'service-overview' | 'service-deployments' | 'service-settings' }
>

interface ServiceAreaRouteContentProps {
  route: ServiceAreaRoute
  incidentServiceAlerts: Record<string, ServiceIncidentBadge>
}

// The service area keeps its own lightweight route handoff so AppShell only
// needs to know that /services/* delegates into one feature boundary.
export function ServiceAreaRouteContent({
  route,
  incidentServiceAlerts,
}: ServiceAreaRouteContentProps) {
  switch (route.kind) {
    case 'service-settings':
      return <ServiceSettingsPage serviceId={route.serviceId} />
    case 'service-overview':
      return (
        <ServiceOverviewPage
          serviceId={route.serviceId}
          incidentServiceAlerts={incidentServiceAlerts}
        />
      )
    case 'service-deployments':
      return <ServiceDeploymentsPage serviceId={route.serviceId} />
    default:
      return null
  }
}
