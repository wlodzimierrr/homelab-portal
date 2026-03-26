import { AppLink } from '@/components/navigation/app-link'
import { PageShell } from '@/components/page-shell'
import { DeploymentsTable } from './components/deployments-table'
import { DeploymentObservabilityPanel } from './components/deployment-observability-panel'
import { useDeploymentHistory } from './use-deployment-history'
import { useDeploymentObservability } from './use-deployment-observability'

interface ServiceDeploymentsPageProps {
  serviceId: string
}

// This page stays responsible for route-level composition only: service identity,
// history/filter state, and selected-deployment observability state are delegated
// to feature hooks so future drill-down work does not pile into one component.
export function ServiceDeploymentsPage({ serviceId }: ServiceDeploymentsPageProps) {
  const history = useDeploymentHistory(serviceId)
  const observability = useDeploymentObservability(history.serviceIdentity, history.selectedDeployment)

  return (
    <PageShell
      title={`Deployments: ${history.normalizedServiceId || 'unknown'}`}
      description="Read-only deployment timeline with post-deploy observability overlays."
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            Showing up to 20 recent deployment records with lifecycle state, Git linkage, and observability deltas.
          </p>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="rounded-full border border-border px-2 py-1">
              Service: {history.serviceIdentity.serviceId}
            </span>
            <span className="rounded-full border border-border px-2 py-1">
              Env: {history.serviceIdentity.env}
            </span>
          </div>
          <AppLink
            to={`/services/${encodeURIComponent(history.normalizedServiceId)}`}
            className="text-sm font-medium text-primary hover:underline"
          >
            Back to overview
          </AppLink>
        </div>

        <DeploymentsTable
          deployments={history.deployments}
          isLoading={history.isLoading}
          error={history.error}
          loadDeployments={history.loadDeployments}
          actionFilter={history.actionFilter}
          setActionFilter={history.setActionFilter}
          availableActions={history.availableActions}
          statusFilter={history.statusFilter}
          setStatusFilter={history.setStatusFilter}
          availableStatuses={history.availableStatuses}
          impactFilterMode={history.impactFilterMode}
          setImpactFilterMode={history.setImpactFilterMode}
          sortMode={history.sortMode}
          setSortMode={history.setSortMode}
          visibleDeployments={history.visibleDeployments}
          hasAnyComparisonWindow={history.hasAnyComparisonWindow}
          selectedDeployment={history.selectedDeployment}
          setSelectedDeploymentId={history.setSelectedDeploymentId}
        />

        <DeploymentObservabilityPanel
          deployments={history.deployments}
          selectedDeployment={history.selectedDeployment}
          observability={observability.observability}
          observabilityLoading={observability.observabilityLoading}
          observabilityError={observability.observabilityError}
          logsPreset={observability.logsPreset}
          setLogsPreset={observability.setLogsPreset}
          loadDeploymentObservability={observability.loadDeploymentObservability}
        />
      </div>
    </PageShell>
  )
}
