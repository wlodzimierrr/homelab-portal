import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { getDeploymentHistory, type DeploymentHistoryItem } from '@/lib/adapters/deployments'
import { evaluateDeploymentHistoryItem } from '@/lib/deployment-alerts'
import { createServiceIdentity } from '@/lib/service-identity'
import { cn } from '@/lib/utils'

interface ServiceDeploymentsPageProps {
  serviceId: string
}

type FilterMode = 'all' | 'regressions' | 'missing'
type SortMode = 'newest' | 'worst_impact'

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
    normalized === 'succeeded' || normalized === 'healthy'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : normalized === 'failed' || normalized === 'degraded' || normalized === 'error'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-muted text-muted-foreground'

  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>
      {outcome}
    </span>
  )
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

function ImpactBadge({ item }: { item: DeploymentHistoryItem }) {
  const alert = evaluateDeploymentHistoryItem(item)

  if (!item.hasComparisonWindow && !alert.suspicious) {
    return (
      <span className="inline-flex items-center rounded-full bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
        Comparison unavailable
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
  const [deployments, setDeployments] = useState<DeploymentHistoryItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [filterMode, setFilterMode] = useState<FilterMode>('all')
  const [sortMode, setSortMode] = useState<SortMode>('newest')

  const loadDeployments = useCallback(async () => {
    setIsLoading(true)
    setError('')
    try {
      const identity = createServiceIdentity({ serviceId: normalizedServiceId })
      const history = await getDeploymentHistory(identity, { limit: 20 })
      setDeployments(history)
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load deployment history'
      setError(message)
      setDeployments([])
    } finally {
      setIsLoading(false)
    }
  }, [normalizedServiceId])

  useEffect(() => {
    void loadDeployments()
  }, [loadDeployments])

  const visibleDeployments = useMemo(() => {
    const filtered = deployments.filter((item) => {
      const alert = evaluateDeploymentHistoryItem(item)

      if (filterMode === 'regressions') {
        return alert.suspicious
      }
      if (filterMode === 'missing') {
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
  }, [deployments, filterMode, sortMode])

  return (
    <PageShell
      title={`Deployments: ${normalizedServiceId || 'unknown'}`}
      description="Read-only deployment timeline with post-deploy observability overlays."
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            Showing up to 20 recent deployments with error, latency, and availability deltas.
          </p>
          <AppLink
            to={`/services/${encodeURIComponent(normalizedServiceId)}`}
            className="text-sm font-medium text-primary hover:underline"
          >
            Back to overview
          </AppLink>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <label className="space-y-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Filter</span>
            <select
              value={filterMode}
              onChange={(event) => setFilterMode(event.target.value as FilterMode)}
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
            description="Try switching filter settings to include more history."
          />
        ) : null}
        {!isLoading && !error && visibleDeployments.length > 0 ? (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="min-w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="px-3 py-2 font-medium text-muted-foreground">Deployed At</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Version / Tag</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Outcome</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Error Rate Delta</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">P95 Latency Delta</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Availability Impact</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Observability</th>
                </tr>
              </thead>
              <tbody>
                {visibleDeployments.map((item) => (
                  <tr key={item.id} className="border-b border-border/70">
                    <td className="px-3 py-2 text-muted-foreground">{formatTimestamp(item.deployedAt)}</td>
                    <td className="px-3 py-2">{item.version}</td>
                    <td className="px-3 py-2">
                      <OutcomeBadge outcome={item.outcome} />
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
                      <ImpactBadge item={item} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </PageShell>
  )
}
