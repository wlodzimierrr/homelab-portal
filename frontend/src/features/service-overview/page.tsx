import type { ServiceIncidentBadge } from '@/lib/incident-alerts'
import { ServiceDetailsPage } from '@/features/service-details/page'

export interface ServiceOverviewPageProps {
  serviceId: string
  incidentServiceAlerts?: Record<string, ServiceIncidentBadge>
}

// The route owner for /services/:id lives here even while the deeper overview
// implementation remains in the legacy service-details feature during migration.
export function ServiceOverviewPage({ serviceId, incidentServiceAlerts }: ServiceOverviewPageProps) {
  return <ServiceDetailsPage serviceId={serviceId} incidentServiceAlerts={incidentServiceAlerts} />
}
