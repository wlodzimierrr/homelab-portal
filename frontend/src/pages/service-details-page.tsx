import { AppLink } from '@/components/navigation/app-link'
import { PageShell } from '@/components/page-shell'
import { Button } from '@/components/ui/button'

interface ServiceDetailsPageProps {
  serviceId: string
}

export function ServiceDetailsPage({ serviceId }: ServiceDetailsPageProps) {

  return (
    <PageShell
      title={`Service: ${serviceId ?? 'unknown'}`}
      description="Single service details and high-level metadata."
    >
      <Button asChild>
        <AppLink to={`/services/${serviceId}/deployments`}>View deployments</AppLink>
      </Button>
    </PageShell>
  )
}
