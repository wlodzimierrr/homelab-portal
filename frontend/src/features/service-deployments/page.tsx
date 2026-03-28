import { AppLink } from '@/components/navigation/app-link'
import { PageShell } from '@/components/page-shell'
import { ToastMessage } from '@/components/toast-message'
import { useCallback, useState } from 'react'
import { useServiceActions } from '@/features/service-details/use-service-actions'
import { DeploymentsTable } from './components/deployments-table'
import { DeploymentObservabilityPanel } from './components/deployment-observability-panel'
import { RollbackPanel } from './components/rollback-panel'
import { useDeploymentHistory } from './use-deployment-history'
import { useDeploymentObservability } from './use-deployment-observability'
import { useServiceActionSupport } from './use-service-action-support'

interface ServiceDeploymentsPageProps {
  serviceId: string
}

// This page stays responsible for route-level composition only: service identity,
// history/filter state, and selected-deployment observability state are delegated
// to feature hooks so future drill-down work does not pile into one component.
export function ServiceDeploymentsPage({ serviceId }: ServiceDeploymentsPageProps) {
  const history = useDeploymentHistory(serviceId)
  const support = useServiceActionSupport(serviceId)
  const observability = useDeploymentObservability(history.serviceIdentity, history.selectedDeployment)
  const [toastMessage, setToastMessage] = useState('')
  const [toastVariant, setToastVariant] = useState<'success' | 'error' | 'info'>('info')
  const actions = useServiceActions({
    serviceId: support.decodedServiceId,
    serviceEnv: history.serviceIdentity.env,
    capabilities: support.capabilities,
    deploymentHistory: history.deployments,
    deploymentLock: support.deploymentLock,
    refreshService: support.loadServiceActionSupport,
    refreshDeployments: history.loadDeployments,
  })

  const handleSubmitRollback = useCallback(async () => {
    try {
      const response = await actions.submitRollbackRequest()
      setToastVariant('success')
      setToastMessage(
        response.status === 'noop'
          ? response.message ?? `Rollback not needed for ${actions.rollbackTargetEnvironment}.`
          : `Rollback request accepted for ${support.decodedServiceId}.`,
      )
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to request service rollback.'
      setToastVariant('error')
      setToastMessage(message)
    }
  }, [actions, support.decodedServiceId])

  return (
    <PageShell
      title={`Deployments: ${history.normalizedServiceId || 'unknown'}`}
      description="Read-only deployment timeline with post-deploy observability overlays."
    >
      <div className="space-y-4">
        {toastMessage ? (
          <ToastMessage
            message={toastMessage}
            variant={toastVariant}
            onClose={() => setToastMessage('')}
          />
        ) : null}
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

        <RollbackPanel
          rollbackSupported={actions.rollbackSupported}
          rollbackEnvs={actions.rollbackEnvs}
          deploymentHistory={history.deployments}
          rollbackTargetEnvironment={actions.rollbackTargetEnvironment}
          setRollbackTargetEnvironment={actions.setRollbackTargetEnvironment}
          rollbackCandidates={actions.rollbackCandidates}
          rollbackCandidatesLoading={actions.rollbackCandidatesLoading}
          rollbackCandidatesError={actions.rollbackCandidatesError}
          selectedRollbackTag={actions.selectedRollbackTag}
          setSelectedRollbackTag={actions.setSelectedRollbackTag}
          rollbackReason={actions.rollbackReason}
          setRollbackReason={actions.setRollbackReason}
          rollbackSubmitting={actions.rollbackSubmitting}
          rollbackError={actions.rollbackError}
          rollbackResult={actions.rollbackResult}
          rollbackLockActive={Boolean(actions.rollbackLockActive)}
          rollbackInFlight={actions.rollbackInFlight}
          onSubmitRollback={() => void handleSubmitRollback()}
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
