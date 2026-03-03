import { PageShell } from '@/components/page-shell'

interface ServiceDeploymentsPageProps {
  serviceId: string
}

export function ServiceDeploymentsPage({ serviceId }: ServiceDeploymentsPageProps) {

  return (
    <PageShell
      title={`Deployments: ${serviceId ?? 'unknown'}`}
      description="Deployment history and rollout state for a service."
    >
      <p className="text-sm text-muted-foreground">Deployment timeline placeholder.</p>
    </PageShell>
  )
}
