import { useCallback, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { cn } from '@/lib/utils'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { StatusCard } from '@/components/status-card'
import { ToastMessage } from '@/components/toast-message'
import { Button } from '@/components/ui/button'
import type { DeploymentHistoryItem } from '@/lib/adapters/deployments'
import type { ServiceMetricsSummary } from '@/lib/adapters/service-metrics'
import { evaluateDeploymentHistoryItem, summarizeDeploymentAlerts } from '@/lib/deployment-alerts'
import type { ServiceIncidentBadge } from '@/lib/incident-alerts'
import {
  buildArgoAppUrl,
  buildGrafanaDashboardLink,
  buildGrafanaErrorPanelLink,
  buildGrafanaLatencyPanelLink,
  buildLogsLink,
} from '@/lib/config'
import { useServiceOverview } from './use-service-overview'
import { useServiceObservability } from './use-service-observability'
import { useServiceActions } from './use-service-actions'
import { ForwardActionsPanel } from './components/forward-actions-panel'
import { OverviewMetricsSummary } from './components/overview-metrics-summary'
import { OverviewObservabilityLinks } from './components/overview-observability-links'

// ServiceDetailsPage is the single-service overview screen. It fans in
// catalog metadata, shallow deployment context, and the most common forward
// deployment actions while delegating deep inspection to external tools.
interface ServiceDetailsPageProps {
  serviceId: string
  incidentServiceAlerts?: Record<string, ServiceIncidentBadge>
}

function formatDate(value?: string) {
  if (!value) {
    return 'N/A'
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return 'N/A'
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)
}

function buildMetricsCoverageMessage(metrics: ServiceMetricsSummary) {
  if (metrics.observabilityDiagnostics?.status && metrics.observabilityDiagnostics.status !== 'ok') {
    return metrics.observabilityDiagnostics.message ?? ''
  }

  const missingLabels = [
    metrics.noData.p95LatencyMs ? 'P95 latency' : null,
    metrics.noData.errorRatePct ? 'error rate' : null,
  ].filter((label): label is string => Boolean(label))

  if (missingLabels.length === 0) {
    return ''
  }

  if (!metrics.noData.uptimePct || !metrics.noData.restartCount) {
    return `${missingLabels.join(' and ')} require service-level HTTP instrumentation. Showing infrastructure-level uptime and restart signals where available.`
  }

  return `${missingLabels.join(' and ')} require service-level HTTP instrumentation. Prometheus is healthy, but this service is not emitting matching request metrics yet.`
}

function IncidentServiceBadge({ alert }: { alert: ServiceIncidentBadge }) {
  const severity = alert.highestSeverity ?? 'info'
  const tone =
    severity === 'critical'
      ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
      : severity === 'warning'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-sky-500/10 text-sky-700 dark:text-sky-300'
  const label = severity === 'critical' ? 'Critical alerts' : severity === 'warning' ? 'Warning alerts' : 'Alerts'

  return (
    <span className={`rounded-md px-2 py-1 text-xs font-medium ${tone}`}>
      {label}: {alert.total}
    </span>
  )
}

export function ServiceDetailsPage({ serviceId, incidentServiceAlerts = {} }: ServiceDetailsPageProps) {
  const {
    decodedServiceId,
    serviceIdentity,
    overview,
    projectContext,
    capabilities,
    deploymentHistory,
    isLoading,
    error,
    loadOverview,
  } = useServiceOverview(serviceId)
  const {
    metrics,
    metricsLoading,
    metricsError,
    loadMetrics,
  } = useServiceObservability(serviceIdentity, {
    loadTrends: false,
    loadTimeline: false,
    loadLogs: false,
  })
  const [toastMessage, setToastMessage] = useState('')
  const [toastVariant, setToastVariant] = useState<'success' | 'error' | 'info'>('info')
  const {
    deploySupported,
    promoteSupported,
    latestDevDeployment,
    latestProdDeployment,
    recentDevTags,
    recentProdTags,
    devInFlight,
    prodInFlight,
    devLockActive,
    prodLockActive,
    deployReason,
    setDeployReason,
    deploySubmitting,
    deployError,
    deployResult,
    promoteReason,
    setPromoteReason,
    promoteSubmitting,
    promoteError,
    promoteResult,
    submitDeployRequest,
    submitPromoteRequest,
  } = useServiceActions({
    serviceId: decodedServiceId,
    serviceEnv: serviceIdentity.env,
    capabilities,
    includeRollback: false,
    deploymentHistory,
    deploymentLock: overview?.deploymentLock ?? null,
    refreshService: loadOverview,
  })

  const handleSubmitDeploy = useCallback(async () => {
    try {
      const response = await submitDeployRequest()
      setToastVariant('success')
      setToastMessage(
        response.status === 'noop'
          ? response.message ?? `Dev already points at ${response.newTag ?? 'the latest image'}.`
          : `Deploy request accepted for ${decodedServiceId}.`,
      )
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to request deploy to dev.'
      setToastVariant('error')
      setToastMessage(message)
    }
  }, [decodedServiceId, submitDeployRequest])

  const handleSubmitPromote = useCallback(async () => {
    try {
      const response = await submitPromoteRequest()
      setToastVariant('success')
      setToastMessage(
        response.status === 'noop'
          ? response.message ?? `Prod already matches ${response.newTag ?? 'the dev tag'}.`
          : `Promote request accepted for ${decodedServiceId}.`,
      )
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to request promote to prod.'
      setToastVariant('error')
      setToastMessage(message)
    }
  }, [decodedServiceId, submitPromoteRequest])

  const argoUrl = useMemo(
    () => buildArgoAppUrl(serviceIdentity.serviceId || decodedServiceId, serviceIdentity.argoAppName),
    [decodedServiceId, serviceIdentity.argoAppName, serviceIdentity.serviceId],
  )
  const grafanaTimeRange = metrics.range
  const grafanaScope = useMemo(
    () => ({
      serviceId: serviceIdentity.serviceId,
      namespace: serviceIdentity.namespace,
      environment: serviceIdentity.env,
      appLabel: serviceIdentity.appLabel,
      argoAppName: serviceIdentity.argoAppName,
      timeRange: grafanaTimeRange,
    }),
    [
      grafanaTimeRange,
      serviceIdentity.appLabel,
      serviceIdentity.argoAppName,
      serviceIdentity.env,
      serviceIdentity.namespace,
      serviceIdentity.serviceId,
    ],
  )
  const grafanaDashboardLink = useMemo(() => buildGrafanaDashboardLink(grafanaScope), [grafanaScope])
  const latencyPanelLink = useMemo(() => buildGrafanaLatencyPanelLink(grafanaScope), [grafanaScope])
  const errorPanelLink = useMemo(() => buildGrafanaErrorPanelLink(grafanaScope), [grafanaScope])
  const logsLink = useMemo(
    () =>
      buildLogsLink({
        serviceId: serviceIdentity.serviceId,
        namespace: serviceIdentity.namespace,
        environment: serviceIdentity.env,
        appLabel: serviceIdentity.appLabel,
        argoAppName: serviceIdentity.argoAppName,
        timeRange: grafanaTimeRange,
        preset: 'all',
        query: `{namespace="${serviceIdentity.namespace}", app="${serviceIdentity.appLabel}"}`,
      }),
    [
      grafanaTimeRange,
      serviceIdentity.appLabel,
      serviceIdentity.argoAppName,
      serviceIdentity.env,
      serviceIdentity.namespace,
      serviceIdentity.serviceId,
    ],
  )
  const deploymentAlert = useMemo(() => {
    const items =
      overview?.deployments.map((deployment) => ({
        outcome: deployment.status,
      })) ?? []
    return summarizeDeploymentAlerts(items)
  }, [overview])
  const effectiveHealth = useMemo(() => {
    if (!overview) {
      return 'unknown' as const
    }
    if (overview.health === 'degraded') {
      return overview.health
    }
    return deploymentAlert.suspicious ? ('degraded' as const) : overview.health
  }, [deploymentAlert.suspicious, overview])
  const incidentAlert = useMemo(
    () => incidentServiceAlerts[decodedServiceId] ?? incidentServiceAlerts[serviceId],
    [decodedServiceId, incidentServiceAlerts, serviceId],
  )
  const metricsCoverageMessage = useMemo(
    () => buildMetricsCoverageMessage(metrics),
    [metrics],
  )

  return (
    <PageShell
      title={`Service: ${decodedServiceId || 'unknown'}`}
      description="Overview for deployment status, endpoints, and recent deployment activity."
    >
      <div className="space-y-6">
        {toastMessage ? (
          <ToastMessage
            message={toastMessage}
            variant={toastVariant}
            onClose={() => setToastMessage('')}
          />
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
          <div className="flex items-center gap-2">
            <span className="rounded-md bg-primary/10 px-2 py-1 text-xs font-medium text-primary">
              Overview
            </span>
            {incidentAlert?.total ? <IncidentServiceBadge alert={incidentAlert} /> : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="outline">
              <AppLink to={`/services/${encodeURIComponent(decodedServiceId)}/settings`}>
                Service settings
              </AppLink>
            </Button>
            <Button asChild variant="outline">
              <AppLink to={`/services/${encodeURIComponent(decodedServiceId)}/deployments`}>
                View deployment history & rollback
              </AppLink>
            </Button>
          </div>
        </div>

        {isLoading ? <LoadingState label="Loading service overview..." rows={4} /> : null}

        {!isLoading && error ? <ErrorState message={error} onRetry={() => void loadOverview()} /> : null}

        {!isLoading && !error && overview ? (
          <>
            <div className="grid gap-3 md:grid-cols-2">
              <article className="rounded-md border border-border bg-background p-4">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Deployed Version</p>
                <p className="mt-2 text-xl font-semibold">{overview.version}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {overview.version && overview.version !== 'N/A'
                    ? 'Resolved from live release metadata.'
                    : 'Deployment metadata is not available for this service yet.'}
                </p>
              </article>
              <OverviewMetricsSummary
                health={effectiveHealth}
                metrics={metrics}
                isLoading={metricsLoading}
                error={metricsError}
                coverageMessage={metricsCoverageMessage}
                onRetry={() => void loadMetrics()}
              />
            </div>

            {projectContext ? (
              <article className="rounded-md border border-border bg-background p-4">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Project</p>
                <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
                  <AppLink
                    to={`/projects#${encodeURIComponent(`${projectContext.projectId}-${serviceIdentity.env || 'dev'}`)}`}
                    className="text-sm font-medium text-primary hover:underline"
                  >
                    {projectContext.projectName}
                  </AppLink>
                  <span className="text-xs text-muted-foreground">
                    Namespace: <span className="font-mono">{projectContext.namespace}</span>
                  </span>
                </div>
                {projectContext.siblingServiceIds.length > 0 ? (
                  <div className="mt-2">
                    <p className="text-xs text-muted-foreground">
                      Sibling services:{' '}
                      {projectContext.siblingServiceIds.map((sid, i) => (
                        <span key={sid}>
                          {i > 0 ? ', ' : ''}
                          <AppLink
                            to={`/services/${encodeURIComponent(sid)}`}
                            className="text-primary hover:underline"
                          >
                            {sid}
                          </AppLink>
                        </span>
                      ))}
                    </p>
                  </div>
                ) : null}
              </article>
            ) : null}

            {(latestDevDeployment ?? latestProdDeployment) ? (
              <section className="space-y-2">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold">Recent Deploy Impact</h2>
                  <AppLink
                    to={`/services/${encodeURIComponent(decodedServiceId)}/deployments`}
                    className="text-xs text-primary hover:underline"
                  >
                    Full history
                  </AppLink>
                </div>
                <div className="space-y-2">
                  {(
                    [
                      latestDevDeployment ? { env: 'dev', item: latestDevDeployment } : null,
                      latestProdDeployment ? { env: 'prod', item: latestProdDeployment } : null,
                    ] as Array<{ env: string; item: DeploymentHistoryItem } | null>
                  ).filter((entry): entry is { env: string; item: DeploymentHistoryItem } => entry !== null).map(({ env, item }) => {
                    const alert = evaluateDeploymentHistoryItem(item)
                    const impactTone =
                      alert.level === 'critical'
                        ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
                        : alert.level === 'warning'
                          ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
                          : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                    const impactLabel =
                      !item.hasComparisonWindow && alert.level === 'none'
                        ? 'No comparison samples'
                        : alert.level === 'critical'
                          ? 'High regression'
                          : alert.level === 'warning'
                            ? 'Regression'
                            : 'Stable/Improved'
                    const outcomeTone =
                      item.outcome === 'live' || item.outcome === 'succeeded'
                        ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                        : item.outcome === 'deploying' || item.outcome === 'pending'
                          ? 'bg-sky-500/10 text-sky-700 dark:text-sky-300'
                          : item.outcome === 'failed' || item.outcome === 'degraded' || item.outcome === 'error'
                            ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
                            : 'bg-muted text-muted-foreground'
                    const errorDelta = item.errorRatePct.delta
                    const latencyDelta = item.p95LatencyMs.delta
                    const availDelta = item.availabilityPct.delta

                    return (
                      <div
                        key={env}
                        className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-background px-3 py-2 text-xs"
                      >
                        <span className="inline-flex rounded-full bg-slate-500/10 px-2 py-0.5 font-medium text-slate-700 dark:text-slate-300">
                          {env}
                        </span>
                        <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 font-medium', outcomeTone)}>
                          {item.outcome}
                        </span>
                        <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 font-medium', impactTone)}>
                          {impactLabel}
                        </span>
                        {item.hasComparisonWindow ? (
                          <span className="text-muted-foreground">
                            err{' '}
                            <span className={typeof errorDelta === 'number' && errorDelta > 0 ? 'text-rose-600 dark:text-rose-400' : ''}>
                              {typeof errorDelta === 'number' ? `${errorDelta >= 0 ? '+' : ''}${errorDelta.toFixed(2)} pp` : 'N/A'}
                            </span>
                            {' · '}p95{' '}
                            <span className={typeof latencyDelta === 'number' && latencyDelta > 0 ? 'text-rose-600 dark:text-rose-400' : ''}>
                              {typeof latencyDelta === 'number' ? `${latencyDelta >= 0 ? '+' : ''}${latencyDelta.toFixed(0)} ms` : 'N/A'}
                            </span>
                            {' · '}avail{' '}
                            <span className={typeof availDelta === 'number' && availDelta < 0 ? 'text-rose-600 dark:text-rose-400' : ''}>
                              {typeof availDelta === 'number' ? `${availDelta >= 0 ? '+' : ''}${availDelta.toFixed(2)} pp` : 'N/A'}
                            </span>
                          </span>
                        ) : null}
                        <span className="ml-auto text-muted-foreground">
                          {item.version ? `v${item.version} · ` : ''}{formatDate(item.deployedAt ?? item.requestedAt)}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </section>
            ) : null}

            {deploymentAlert.suspicious ? (
              <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
                <p className="text-sm font-medium text-amber-800 dark:text-amber-300">
                  Suspicious deployment activity detected
                </p>
                <p className="text-xs text-amber-700/90 dark:text-amber-200">
                  Latest deployments triggered alert rules. Service status is highlighted as degraded.
                </p>
              </div>
            ) : null}

            {overview.deploymentLock ? (
              <div className="rounded-md border border-sky-500/50 bg-sky-500/10 p-3">
                <p className="text-sm font-medium text-sky-900 dark:text-sky-200">
                  Active deployment lock for {overview.deploymentLock.action}
                </p>
                <p className="mt-1 text-xs text-sky-900 dark:text-sky-200">
                  Overlapping portal mutations for this service/environment should be treated as blocked until the
                  active request finishes or the stale lock expires.
                </p>
                <div className="mt-2 space-y-1 text-xs text-sky-900 dark:text-sky-200">
                  <p>Requested: {formatDate(overview.deploymentLock.requestedAt)}</p>
                  <p>Expires: {formatDate(overview.deploymentLock.expiresAt)}</p>
                  {overview.deploymentLock.deployReason ? (
                    <p>Reason: {overview.deploymentLock.deployReason}</p>
                  ) : null}
                  {overview.deploymentLock.gitPrUrl ? (
                    <a
                      href={overview.deploymentLock.gitPrUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex font-medium text-sky-900 underline underline-offset-2 dark:text-sky-200"
                    >
                      GitOps PR #{overview.deploymentLock.gitPrNumber ?? 'link'}
                    </a>
                  ) : null}
                </div>
              </div>
            ) : null}

            {import.meta.env?.DEV ? (
              <section className="space-y-3">
                <h2 className="text-sm font-semibold">Status Visual Checks</h2>
                <div className="grid gap-3 md:grid-cols-3">
                  <StatusCard health="healthy" sync="synced" />
                  <StatusCard health="degraded" sync="out_of_sync" />
                  <StatusCard />
                </div>
              </section>
            ) : null}

            <OverviewObservabilityLinks
              argoUrl={argoUrl}
              grafanaDashboardLink={grafanaDashboardLink}
              latencyPanelLink={latencyPanelLink}
              errorPanelLink={errorPanelLink}
              logsLink={logsLink}
            />

            <section className="space-y-3">
              <h2 className="text-sm font-semibold">Endpoints</h2>
              {overview.endpoints.length === 0 ? (
                <div className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
                  <p className="font-medium">
                    {overview.endpointState === 'metadata_missing'
                      ? 'Endpoint metadata is unavailable right now.'
                      : 'No routed public or internal endpoints are configured for this service.'}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {overview.endpointState === 'metadata_missing'
                      ? 'The live metadata sources did not return endpoint information for this service.'
                      : 'This can be expected for internal or service-only components such as oauth2-proxy.'}
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {overview.endpoints.map((endpoint) => (
                    <div
                      key={`${endpoint.type ?? 'unknown'}-${endpoint.url}`}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-background p-3"
                    >
                      <div>
                        <p className="text-sm font-medium">{endpoint.label ?? endpoint.type ?? 'endpoint'}</p>
                        <p className="text-xs text-muted-foreground">{endpoint.type ?? 'unknown'}</p>
                      </div>
                      <a
                        href={endpoint.url}
                        target="_blank"
                        rel="noreferrer"
                        className="break-all text-sm text-primary hover:underline"
                      >
                        {endpoint.url}
                      </a>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <ForwardActionsPanel
              serviceId={decodedServiceId}
              deploySupported={deploySupported}
              promoteSupported={promoteSupported}
              latestDevDeployment={latestDevDeployment}
              latestProdDeployment={latestProdDeployment}
              recentDevTags={recentDevTags}
              recentProdTags={recentProdTags}
              devLockActive={Boolean(devLockActive)}
              prodLockActive={Boolean(prodLockActive)}
              devInFlight={devInFlight}
              prodInFlight={prodInFlight}
              deployReason={deployReason}
              setDeployReason={setDeployReason}
              deploySubmitting={deploySubmitting}
              deployError={deployError}
              deployResult={deployResult}
              onSubmitDeploy={() => void handleSubmitDeploy()}
              promoteReason={promoteReason}
              setPromoteReason={setPromoteReason}
              promoteSubmitting={promoteSubmitting}
              promoteError={promoteError}
              promoteResult={promoteResult}
              onSubmitPromote={() => void handleSubmitPromote()}
            />
          </>
        ) : null}
      </div>
    </PageShell>
  )
}
