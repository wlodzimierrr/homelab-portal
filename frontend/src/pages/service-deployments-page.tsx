import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { ServiceHealthTimeline } from '@/components/service-health-timeline'
import {
  getDeploymentObservability,
  type DeploymentObservability,
  type DeploymentObservabilityMetricSnapshot,
} from '@/lib/adapters/deployment-observability'
import { getDeploymentHistory, type DeploymentHistoryItem } from '@/lib/adapters/deployments'
import type { LogsQuickViewPreset } from '@/lib/adapters/logs-quickview'
import { evaluateDeploymentHistoryItem } from '@/lib/deployment-alerts'
import { createServiceIdentity } from '@/lib/service-identity'
import { cn } from '@/lib/utils'

interface ServiceDeploymentsPageProps {
  serviceId: string
}

type ImpactFilterMode = 'all' | 'regressions' | 'missing'
type SortMode = 'newest' | 'worst_impact'
type DeploymentLogsPreset = LogsQuickViewPreset

const deploymentLogsPresetOptions: Array<{ value: DeploymentLogsPreset; label: string }> = [
  { value: 'errors', label: 'Errors' },
  { value: 'warnings', label: 'Warnings' },
  { value: 'restarts', label: 'Restarts' },
]

function formatTimestamp(value?: string) {
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

function normalizeServiceId(rawServiceId: string) {
  try {
    return decodeURIComponent(rawServiceId)
  } catch {
    return rawServiceId
  }
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const normalized = outcome.toLowerCase()
  const tone =
    normalized === 'live' || normalized === 'succeeded' || normalized === 'healthy'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : normalized === 'deploying' || normalized === 'pending'
        ? 'bg-sky-500/10 text-sky-700 dark:text-sky-300'
      : normalized === 'failed' || normalized === 'degraded' || normalized === 'error'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-muted text-muted-foreground'

  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>
      {outcome}
    </span>
  )
}

function formatAction(action: string) {
  if (action === 'config-change') {
    return 'Config change'
  }
  return action.charAt(0).toUpperCase() + action.slice(1)
}

function formatDelta(unit: 'pct' | 'ms', value?: number) {
  if (typeof value !== 'number') {
    return 'N/A'
  }

  const signed = value >= 0 ? `+${value.toFixed(unit === 'ms' ? 0 : 2)}` : value.toFixed(unit === 'ms' ? 0 : 2)
  if (unit === 'ms') {
    return `${signed} ms`
  }

  return `${signed} pp`
}

function formatBeforeAfter(unit: 'pct' | 'ms', before?: number, after?: number) {
  if (typeof before !== 'number' || typeof after !== 'number') {
    return 'Unavailable'
  }

  if (unit === 'ms') {
    return `${before.toFixed(0)} -> ${after.toFixed(0)} ms`
  }

  return `${before.toFixed(2)}% -> ${after.toFixed(2)}%`
}

function formatWindowRange(start?: string, end?: string) {
  if (!start || !end) {
    return 'Window unavailable'
  }
  return `${formatTimestamp(start)} -> ${formatTimestamp(end)}`
}

function isDeploymentRecordId(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
}

function shiftTimestamp(value: string, minutes: number) {
  const timestamp = Date.parse(value)
  if (Number.isNaN(timestamp)) {
    return null
  }

  return new Date(timestamp + minutes * 60_000).toISOString()
}

function buildDeploymentObservabilityRequest(item: DeploymentHistoryItem) {
  if (isDeploymentRecordId(item.id)) {
    return { deploymentId: item.id }
  }

  const windowStart = item.requestedAt ?? item.deployedAt
  const windowEnd = item.deployedAt ?? item.requestedAt

  if (windowStart && windowEnd) {
    const startMs = Date.parse(windowStart)
    const endMs = Date.parse(windowEnd)

    if (!Number.isNaN(startMs) && !Number.isNaN(endMs) && endMs > startMs) {
      return { windowStart, windowEnd }
    }

    const widenedWindowEnd = shiftTimestamp(windowStart, 5)
    if (widenedWindowEnd) {
      return { windowStart, windowEnd: widenedWindowEnd }
    }
  }

  if (windowStart) {
    const widenedWindowEnd = shiftTimestamp(windowStart, 5)
    if (widenedWindowEnd) {
      return { windowStart, windowEnd: widenedWindowEnd }
    }
  }

  if (windowEnd) {
    const widenedWindowStart = shiftTimestamp(windowEnd, -5)
    if (widenedWindowStart) {
      return { windowStart: widenedWindowStart, windowEnd }
    }
  }

  return { deploymentId: item.id }
}

function ObservabilityStatusBadge({
  status,
}: {
  status: 'ok' | 'no_data' | 'no_deployment_window'
}) {
  const tone =
    status === 'ok'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : status === 'no_data'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-slate-500/10 text-slate-700 dark:text-slate-300'
  const label =
    status === 'ok' ? 'Window scoped' : status === 'no_data' ? 'No telemetry retained' : 'No deploy window'
  return <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>{label}</span>
}

function DeploymentMetricWindowCard({
  label,
  snapshot,
}: {
  label: string
  snapshot?: DeploymentObservabilityMetricSnapshot
}) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-2 text-sm">{formatBeforeAfter(label.includes('Latency') ? 'ms' : 'pct', snapshot?.before, snapshot?.after)}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        delta: {formatDelta(label.includes('Latency') ? 'ms' : 'pct', snapshot?.delta)}
      </p>
    </div>
  )
}

function ImpactBadge({ item }: { item: DeploymentHistoryItem }) {
  const alert = evaluateDeploymentHistoryItem(item)

  if (!item.hasComparisonWindow && !alert.suspicious) {
    return (
      <span className="inline-flex items-center rounded-full bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
        No comparison samples
      </span>
    )
  }

  const tone =
    alert.level === 'critical'
      ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
      : alert.level === 'warning'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'

  const label = alert.level === 'critical' ? 'High regression' : alert.level === 'warning' ? 'Regression' : 'Stable/Improved'

  return <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>{label}</span>
}

export function ServiceDeploymentsPage({ serviceId }: ServiceDeploymentsPageProps) {
  const normalizedServiceId = useMemo(() => normalizeServiceId(serviceId), [serviceId])
  const serviceIdentity = useMemo(
    () => createServiceIdentity({ serviceId: normalizedServiceId }),
    [normalizedServiceId],
  )
  const [deployments, setDeployments] = useState<DeploymentHistoryItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [impactFilterMode, setImpactFilterMode] = useState<ImpactFilterMode>('all')
  const [actionFilter, setActionFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortMode, setSortMode] = useState<SortMode>('newest')
  const [selectedDeploymentId, setSelectedDeploymentId] = useState<string | null>(null)
  const [observability, setObservability] = useState<DeploymentObservability | null>(null)
  const [observabilityLoading, setObservabilityLoading] = useState(false)
  const [observabilityError, setObservabilityError] = useState('')
  const [logsPreset, setLogsPreset] = useState<DeploymentLogsPreset>('errors')

  const loadDeployments = useCallback(async () => {
    setIsLoading(true)
    setError('')
    try {
      const history = await getDeploymentHistory(serviceIdentity, { limit: 20 })
      setDeployments(history)
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load deployment history'
      setError(message)
      setDeployments([])
    } finally {
      setIsLoading(false)
    }
  }, [serviceIdentity])

  useEffect(() => {
    void loadDeployments()
  }, [loadDeployments])

  const availableActions = useMemo(() => {
    return [...new Set(deployments.map((item) => item.action).filter(Boolean))].sort((left, right) =>
      left.localeCompare(right),
    )
  }, [deployments])

  const availableStatuses = useMemo(() => {
    return [...new Set(deployments.map((item) => item.outcome).filter(Boolean))].sort((left, right) =>
      left.localeCompare(right),
    )
  }, [deployments])

  const visibleDeployments = useMemo(() => {
    const filtered = deployments.filter((item) => {
      const alert = evaluateDeploymentHistoryItem(item)

      if (actionFilter !== 'all' && item.action !== actionFilter) {
        return false
      }
      if (statusFilter !== 'all' && item.outcome !== statusFilter) {
        return false
      }
      if (impactFilterMode === 'regressions') {
        return alert.suspicious
      }
      if (impactFilterMode === 'missing') {
        return !item.hasComparisonWindow && !alert.suspicious
      }
      return true
    })

    return [...filtered].sort((a, b) => {
      if (sortMode === 'worst_impact') {
        const leftAlert = evaluateDeploymentHistoryItem(a)
        const rightAlert = evaluateDeploymentHistoryItem(b)

        if (rightAlert.priority !== leftAlert.priority) {
          return rightAlert.priority - leftAlert.priority
        }

        if (b.regressionScore !== a.regressionScore) {
          return b.regressionScore - a.regressionScore
        }
      }
      const left = a.deployedAt ? new Date(a.deployedAt).getTime() : 0
      const right = b.deployedAt ? new Date(b.deployedAt).getTime() : 0
      return right - left
    })
  }, [actionFilter, deployments, impactFilterMode, sortMode, statusFilter])

  const hasAnyComparisonWindow = useMemo(
    () => deployments.some((item) => item.hasComparisonWindow),
    [deployments],
  )

  const selectedDeployment = useMemo(() => {
    if (!selectedDeploymentId || visibleDeployments.length === 0) {
      return visibleDeployments[0] ?? null
    }
    return visibleDeployments.find((item) => item.id === selectedDeploymentId) ?? visibleDeployments[0] ?? null
  }, [selectedDeploymentId, visibleDeployments])

  useEffect(() => {
    if (visibleDeployments.length === 0) {
      if (selectedDeploymentId !== null) {
        setSelectedDeploymentId(null)
      }
      return
    }

    if (!selectedDeploymentId) {
      setSelectedDeploymentId(visibleDeployments[0].id)
      return
    }

    if (!visibleDeployments.some((item) => item.id === selectedDeploymentId)) {
      setSelectedDeploymentId(visibleDeployments[0].id)
    }
  }, [selectedDeploymentId, visibleDeployments])

  const loadDeploymentObservability = useCallback(async () => {
    if (!selectedDeployment) {
      setObservability(null)
      setObservabilityError('')
      return
    }
    setObservabilityLoading(true)
    setObservabilityError('')
    try {
      const response = await getDeploymentObservability(serviceIdentity, {
        ...buildDeploymentObservabilityRequest(selectedDeployment),
        logsPreset,
        logsLimit: 50,
      })
      setObservability(response)
    } catch (requestError) {
      const message =
        requestError instanceof Error
          ? requestError.message
          : 'Failed to load deployment-scoped observability.'
      setObservabilityError(message)
      setObservability(null)
    } finally {
      setObservabilityLoading(false)
    }
  }, [logsPreset, selectedDeployment, serviceIdentity])

  useEffect(() => {
    void loadDeploymentObservability()
  }, [loadDeploymentObservability])

  return (
    <PageShell
      title={`Deployments: ${normalizedServiceId || 'unknown'}`}
      description="Read-only deployment timeline with post-deploy observability overlays."
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            Showing up to 20 recent deployment records with lifecycle state, Git linkage, and observability deltas.
          </p>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="rounded-full border border-border px-2 py-1">
              Service: {serviceIdentity.serviceId}
            </span>
            <span className="rounded-full border border-border px-2 py-1">Env: {serviceIdentity.env}</span>
          </div>
          <AppLink
            to={`/services/${encodeURIComponent(normalizedServiceId)}`}
            className="text-sm font-medium text-primary hover:underline"
          >
            Back to overview
          </AppLink>
        </div>

        <div className="grid gap-3 md:grid-cols-4">
          <label className="space-y-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Action</span>
            <select
              value={actionFilter}
              onChange={(event) => setActionFilter(event.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              <option value="all">All actions</option>
              {availableActions.map((action) => (
                <option key={action} value={action}>
                  {formatAction(action)}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Status</span>
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              <option value="all">All statuses</option>
              {availableStatuses.map((status) => (
                <option key={status} value={status}>
                  {formatAction(status)}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Impact</span>
            <select
              value={impactFilterMode}
              onChange={(event) => setImpactFilterMode(event.target.value as ImpactFilterMode)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              <option value="all">All deployments</option>
              <option value="regressions">Regressions only</option>
              <option value="missing">Missing comparisons</option>
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Sort</span>
            <select
              value={sortMode}
              onChange={(event) => setSortMode(event.target.value as SortMode)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              <option value="newest">Newest first</option>
              <option value="worst_impact">Worst impact first</option>
            </select>
          </label>
        </div>

        {isLoading ? <LoadingState label="Loading deployments..." rows={5} /> : null}
        {!isLoading && error ? <ErrorState message={error} onRetry={() => void loadDeployments()} /> : null}
        {!isLoading && !error && deployments.length === 0 ? (
          <EmptyState title="No deployments found for this service yet." />
        ) : null}
        {!isLoading && !error && deployments.length > 0 && visibleDeployments.length === 0 ? (
          <EmptyState
            title="No deployments match current filters."
            description="Try broadening the action, status, or impact filters to include more history."
          />
        ) : null}
        {!isLoading && !error && deployments.length > 0 && !hasAnyComparisonWindow ? (
          <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
            <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
              Comparison metrics are not available for these deployment windows.
            </p>
            <p className="mt-1 text-xs text-amber-900 dark:text-amber-200">
              Prometheus has no retained samples for the selected deployment periods. This usually means the
              deployment is older than the current metrics retention window or happened before service-level
              request metrics were available.
            </p>
          </div>
        ) : null}
        {!isLoading && !error && visibleDeployments.length > 0 ? (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="min-w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="px-3 py-2 font-medium text-muted-foreground">Requested / Completed</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Action / Version</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Status</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Error Rate Delta</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">P95 Latency Delta</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Availability Impact</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Git / Reason</th>
                </tr>
              </thead>
              <tbody>
                {visibleDeployments.map((item) => (
                  <tr
                    key={item.id}
                    className={cn(
                      'border-b border-border/70',
                      selectedDeployment?.id === item.id ? 'bg-primary/5' : undefined,
                    )}
                  >
                    <td className="px-3 py-2">
                      <p className="text-muted-foreground">{formatTimestamp(item.requestedAt)}</p>
                      <p className="text-xs text-muted-foreground">completed: {formatTimestamp(item.deployedAt)}</p>
                    </td>
                    <td className="px-3 py-2">
                      <p className="font-medium">{formatAction(item.action)}</p>
                      <p>{item.version}</p>
                      <button
                        type="button"
                        onClick={() => setSelectedDeploymentId(item.id)}
                        className="mt-1 text-xs font-medium text-primary hover:underline"
                      >
                        {selectedDeployment?.id === item.id ? 'Inspecting deploy window' : 'Inspect deploy window'}
                      </button>
                      {item.argoApp ? <p className="text-xs text-muted-foreground">Argo: {item.argoApp}</p> : null}
                    </td>
                    <td className="px-3 py-2">
                      <div className="space-y-1">
                        <OutcomeBadge outcome={item.outcome} />
                        {item.failureReason ? (
                          <p className="max-w-xs text-xs text-amber-700 dark:text-amber-300">{item.failureReason}</p>
                        ) : null}
                        {!item.failureReason && (item.syncStatus || item.healthStatus) ? (
                          <p className="text-xs text-muted-foreground">
                            sync: {item.syncStatus ?? 'unknown'} / health: {item.healthStatus ?? 'unknown'}
                          </p>
                        ) : null}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <p>{formatBeforeAfter('pct', item.errorRatePct.before, item.errorRatePct.after)}</p>
                      <p className="text-xs text-muted-foreground">
                        delta: {formatDelta('pct', item.errorRatePct.delta)}
                      </p>
                    </td>
                    <td className="px-3 py-2">
                      <p>{formatBeforeAfter('ms', item.p95LatencyMs.before, item.p95LatencyMs.after)}</p>
                      <p className="text-xs text-muted-foreground">
                        delta: {formatDelta('ms', item.p95LatencyMs.delta)}
                      </p>
                    </td>
                    <td className="px-3 py-2">
                      <p>{formatBeforeAfter('pct', item.availabilityPct.before, item.availabilityPct.after)}</p>
                      <p className="text-xs text-muted-foreground">
                        delta: {formatDelta('pct', item.availabilityPct.delta)}
                      </p>
                    </td>
                    <td className="px-3 py-2">
                      <div className="space-y-1">
                        <ImpactBadge item={item} />
                        {item.gitPrUrl ? (
                          <a
                            href={item.gitPrUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="block text-xs text-primary hover:underline"
                          >
                            PR #{item.gitPrNumber ?? 'link'}
                          </a>
                        ) : null}
                        {item.compareUrl ? (
                          <a
                            href={item.compareUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="block text-xs text-primary hover:underline"
                          >
                            Compare / diff
                          </a>
                        ) : null}
                        {item.deployReason ? (
                          <p className="max-w-xs text-xs text-muted-foreground">{item.deployReason}</p>
                        ) : null}
                        {!item.deployReason && item.gitRef ? (
                          <p className="max-w-xs text-xs text-muted-foreground">{item.gitRef}</p>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {!isLoading && !error && deployments.length > 0 ? (
          <section className="space-y-3 rounded-md border border-border bg-background p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold">Deploy Window Observability</h2>
                <p className="text-xs text-muted-foreground">
                  Logs, metrics, and health timeline anchored to the selected deployment record.
                </p>
              </div>
              <div className="text-right text-xs text-muted-foreground">
                <p>{selectedDeployment ? `${formatAction(selectedDeployment.action)} ${selectedDeployment.version}` : 'No deployment selected'}</p>
                <p>{observability?.context ? formatWindowRange(observability.context.windowStart, observability.context.windowEnd) : 'Select a deployment row to inspect.'}</p>
              </div>
            </div>

            {selectedDeployment ? (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <OutcomeBadge outcome={selectedDeployment.outcome} />
                {observability ? <ObservabilityStatusBadge status={observability.metrics.queryStatus} /> : null}
                {observability?.context.deployReason ? (
                  <span className="text-muted-foreground">{observability.context.deployReason}</span>
                ) : null}
              </div>
            ) : null}

            {observabilityLoading ? <LoadingState label="Loading deploy-window observability..." rows={4} /> : null}
            {!observabilityLoading && observabilityError ? (
              <ErrorState message={observabilityError} onRetry={() => void loadDeploymentObservability()} />
            ) : null}

            {!observabilityLoading && !observabilityError && observability ? (
              <div className="space-y-4">
                {observability.context.evidenceStatus === 'missing' ? (
                  <div className="rounded-md border border-slate-500/40 bg-slate-500/10 p-3">
                    <p className="text-sm font-medium text-slate-900 dark:text-slate-200">
                      Deployment window evidence is missing for this record.
                    </p>
                    <p className="mt-1 text-xs text-slate-900 dark:text-slate-200">
                      {observability.context.evidenceMessage ?? 'This deployment record does not expose a usable deploy window yet.'}
                    </p>
                  </div>
                ) : null}

                <section className="space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold">Anchored Metrics</h3>
                    <ObservabilityStatusBadge status={observability.metrics.queryStatus} />
                  </div>
                  {observability.metrics.queryMessage ? (
                    <p className="text-xs text-muted-foreground">{observability.metrics.queryMessage}</p>
                  ) : null}
                  <div className="grid gap-3 md:grid-cols-3">
                    <DeploymentMetricWindowCard
                      label="Error Rate"
                      snapshot={observability.metrics.errorRatePct}
                    />
                    <DeploymentMetricWindowCard
                      label="P95 Latency"
                      snapshot={observability.metrics.p95LatencyMs}
                    />
                    <DeploymentMetricWindowCard
                      label="Availability"
                      snapshot={observability.metrics.availabilityPct}
                    />
                  </div>
                </section>

                <section className="space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold">Deploy Health Timeline</h3>
                    <ObservabilityStatusBadge status={observability.healthTimeline.queryStatus} />
                  </div>
                  {observability.healthTimeline.queryMessage ? (
                    <p className="text-xs text-muted-foreground">{observability.healthTimeline.queryMessage}</p>
                  ) : null}
                  <ServiceHealthTimeline
                    segments={observability.healthTimeline.segments}
                    lastRefreshedAt={observability.healthTimeline.generatedAt}
                    isLoading={false}
                  />
                </section>

                <section className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold">Deploy Logs Quick View</h3>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      Preset
                      <select
                        value={logsPreset}
                        onChange={(event) => setLogsPreset(event.target.value as DeploymentLogsPreset)}
                        className="rounded-md border border-border bg-background px-2 py-1 text-xs"
                      >
                        {deploymentLogsPresetOptions.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <ObservabilityStatusBadge status={observability.logsQuickView.queryStatus} />
                    {observability.logsQuickView.queryMessage ? (
                      <p className="text-xs text-muted-foreground">{observability.logsQuickView.queryMessage}</p>
                    ) : null}
                  </div>
                  {observability.logsQuickView.lines.length > 0 ? (
                    <div className="overflow-hidden rounded-md border border-border">
                      <div className="max-h-80 overflow-auto">
                        <table className="min-w-full text-left text-sm">
                          <thead>
                            <tr className="border-b border-border bg-muted/30">
                              <th className="px-3 py-2 font-medium text-muted-foreground">Timestamp</th>
                              <th className="px-3 py-2 font-medium text-muted-foreground">Message</th>
                            </tr>
                          </thead>
                          <tbody>
                            {observability.logsQuickView.lines.map((line) => (
                              <tr key={`${line.timestamp}-${line.message}`} className="border-b border-border/50 align-top">
                                <td className="px-3 py-2 text-xs text-muted-foreground">{formatTimestamp(line.timestamp)}</td>
                                <td className="px-3 py-2">
                                  <pre className="whitespace-pre-wrap break-words font-mono text-xs">{line.message}</pre>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : (
                    <div className="rounded-md border border-dashed border-border bg-muted/10 p-3 text-sm text-muted-foreground">
                      No log lines were retained for this deployment window and preset.
                    </div>
                  )}
                </section>
              </div>
            ) : null}
          </section>
        ) : null}
      </div>
    </PageShell>
  )
}
