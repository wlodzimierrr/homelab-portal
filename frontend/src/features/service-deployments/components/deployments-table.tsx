import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import type { DeploymentHistoryItem } from '@/lib/adapters/deployments'
import { cn } from '@/lib/utils'
import {
  formatAction,
  formatBeforeAfter,
  formatDelta,
  formatTimestamp,
  type ImpactFilterMode,
  type SortMode,
} from '../shared'
import {
  EvidenceBadge,
  ImpactBadge,
  MetricsSourceBadge,
  OutcomeBadge,
} from '../shared-components'

interface DeploymentsTableProps {
  deployments: DeploymentHistoryItem[]
  isLoading: boolean
  error: string
  loadDeployments: () => Promise<void>
  actionFilter: string
  setActionFilter: (value: string) => void
  availableActions: string[]
  statusFilter: string
  setStatusFilter: (value: string) => void
  availableStatuses: string[]
  impactFilterMode: ImpactFilterMode
  setImpactFilterMode: (value: ImpactFilterMode) => void
  sortMode: SortMode
  setSortMode: (value: SortMode) => void
  visibleDeployments: DeploymentHistoryItem[]
  hasAnyComparisonWindow: boolean
  selectedDeployment: DeploymentHistoryItem | null
  setSelectedDeploymentId: (value: string | null) => void
}

export function DeploymentsTable({
  deployments,
  isLoading,
  error,
  loadDeployments,
  actionFilter,
  setActionFilter,
  availableActions,
  statusFilter,
  setStatusFilter,
  availableStatuses,
  impactFilterMode,
  setImpactFilterMode,
  sortMode,
  setSortMode,
  visibleDeployments,
  hasAnyComparisonWindow,
  selectedDeployment,
  setSelectedDeploymentId,
}: DeploymentsTableProps) {
  return (
    <>
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
                    <p className="text-xs text-muted-foreground">
                      completed: {formatTimestamp(item.deployedAt)}
                    </p>
                  </td>
                  <td className="px-3 py-2">
                    <p className="font-medium">{formatAction(item.action)}</p>
                    <p>{item.version}</p>
                    <button
                      type="button"
                      onClick={() => setSelectedDeploymentId(item.id)}
                      className="mt-1 text-xs font-medium text-primary hover:underline"
                    >
                      {selectedDeployment?.id === item.id
                        ? 'Inspecting deploy window'
                        : 'Inspect deploy window'}
                    </button>
                    {item.argoApp ? (
                      <p className="text-xs text-muted-foreground">Argo: {item.argoApp}</p>
                    ) : null}
                  </td>
                  <td className="px-3 py-2">
                    <div className="space-y-1">
                      <EvidenceBadge
                        source={item.evidenceSource}
                        hasData={Boolean(item.deployedAt || item.version !== 'N/A')}
                        backfilled={item.metadata?.backfilled === true}
                      />
                      <OutcomeBadge outcome={item.outcome} />
                      {item.failureReason ? (
                        <p className="max-w-xs text-xs text-amber-700 dark:text-amber-300">
                          {item.failureReason}
                        </p>
                      ) : null}
                      {!item.failureReason && (item.syncStatus || item.healthStatus) ? (
                        <p className="text-xs text-muted-foreground">
                          sync: {item.syncStatus ?? 'unknown'} / health:{' '}
                          {item.healthStatus ?? 'unknown'}
                        </p>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <p>{formatBeforeAfter('pct', item.errorRatePct.before, item.errorRatePct.after)}</p>
                    <p className="text-xs text-muted-foreground">
                      delta: {formatDelta('pct', item.errorRatePct.delta)}
                    </p>
                    {item.metricsSource !== 'live_query' ? (
                      <div className="mt-1">
                        <MetricsSourceBadge source={item.metricsSource} />
                      </div>
                    ) : null}
                  </td>
                  <td className="px-3 py-2">
                    <p>{formatBeforeAfter('ms', item.p95LatencyMs.before, item.p95LatencyMs.after)}</p>
                    <p className="text-xs text-muted-foreground">
                      delta: {formatDelta('ms', item.p95LatencyMs.delta)}
                    </p>
                  </td>
                  <td className="px-3 py-2">
                    <p>
                      {formatBeforeAfter(
                        'pct',
                        item.availabilityPct.before,
                        item.availabilityPct.after,
                      )}
                    </p>
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
    </>
  )
}
