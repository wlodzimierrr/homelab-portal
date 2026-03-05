import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { isApiAuthDiagnosticError, type ApiAuthDiagnostic } from '@/lib/api'
import {
  getReleaseDashboardEntries,
  type ReleaseDashboardEntry,
  type ReleaseHealthStatus,
  type ReleaseDashboardSource,
  type ReleaseDashboardLiveStatus,
  type ReleaseSyncStatus,
} from '@/lib/adapters/release-dashboard'
import { cn } from '@/lib/utils'

type DriftFilter = 'all' | 'drift_only'

function shortSha(value?: string) {
  if (!value) {
    return 'N/A'
  }
  return value.slice(0, 8)
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

function SyncBadge({ status }: { status: ReleaseSyncStatus }) {
  const tone =
    status === 'synced'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : status === 'out_of_sync'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-muted text-muted-foreground'

  const label = status.replace(/_/g, ' ')
  return <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium capitalize', tone)}>{label}</span>
}

function HealthBadge({ status }: { status: ReleaseHealthStatus }) {
  const tone =
    status === 'healthy'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : status === 'degraded'
        ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
        : 'bg-muted text-muted-foreground'

  return <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium capitalize', tone)}>{status}</span>
}

function DriftBadge({ drift }: { drift: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex rounded-full px-2 py-1 text-xs font-medium',
        drift
          ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
          : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
      )}
    >
      {drift ? 'Drift detected' : 'In sync'}
    </span>
  )
}

function SummaryCard({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <article className="rounded-md border border-border bg-background p-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn('mt-2 text-2xl font-semibold', tone)}>{value}</p>
    </article>
  )
}

function DataSourceBadge({ source }: { source: ReleaseDashboardSource }) {
  const config =
    source === 'live_api'
      ? { label: 'Live', tone: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' }
      : { label: 'Fallback: Projects', tone: 'bg-amber-500/10 text-amber-700 dark:text-amber-300' }

  return <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium', config.tone)}>{config.label}</span>
}

function LiveStatusBadge({ status }: { status: ReleaseDashboardLiveStatus }) {
  const config =
    status === 'live_api'
      ? { label: 'Live source: API', tone: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' }
      : { label: 'Live source: Projects fallback', tone: 'bg-amber-500/10 text-amber-700 dark:text-amber-300' }

  return <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium', config.tone)}>{config.label}</span>
}

function UnknownValue({ source }: { source: ReleaseDashboardSource }) {
  const label = source === 'live_api' ? 'Upstream unknown' : 'Unavailable from fallback source'
  return <span className="text-xs text-muted-foreground">{label}</span>
}

function UnknownCountCard({ count, source }: { count: number; source: ReleaseDashboardSource }) {
  const label = source === 'live_api' ? 'Unknown upstream fields' : 'Unknown source fields'
  const tone = count > 0 ? 'text-amber-700 dark:text-amber-300' : 'text-emerald-700 dark:text-emerald-300'
  return <SummaryCard label={label} value={String(count)} tone={tone} />
}

export function DashboardPage() {
  const [rows, setRows] = useState<ReleaseDashboardEntry[]>([])
  const [dataSource, setDataSource] = useState<ReleaseDashboardSource>('projects_fallback')
  const [liveStatus, setLiveStatus] = useState<ReleaseDashboardLiveStatus>('fallback_projects')
  const [unknownFieldCount, setUnknownFieldCount] = useState(0)
  const [warnings, setWarnings] = useState<string[]>([])
  const [authDiagnostic, setAuthDiagnostic] = useState<ApiAuthDiagnostic | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [driftFilter, setDriftFilter] = useState<DriftFilter>('all')

  const loadDashboard = useCallback(async () => {
    setIsLoading(true)
    setError('')
    setAuthDiagnostic(null)

    try {
      const result = await getReleaseDashboardEntries()
      setRows(result.rows)
      setDataSource(result.dataSource)
      setLiveStatus(result.liveStatus)
      setUnknownFieldCount(result.unknownFieldCount)
      setWarnings(result.warnings)
    } catch (requestError) {
      if (isApiAuthDiagnosticError(requestError)) {
        setAuthDiagnostic(requestError.diagnostic)
        setError('')
      } else {
        const message = requestError instanceof Error ? requestError.message : 'Failed to load release dashboard'
        setError(message)
      }
      setLiveStatus('fallback_projects')
      setUnknownFieldCount(0)
      setWarnings([])
      setRows([])
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadDashboard()
  }, [loadDashboard])

  const filteredRows = useMemo(() => {
    const query = search.trim().toLowerCase()

    return rows.filter((row) => {
      if (driftFilter === 'drift_only' && !row.drift) {
        return false
      }

      if (!query) {
        return true
      }

      const searchable = [
        row.serviceName,
        row.serviceId,
        row.environment,
        row.commitSha ?? '',
        row.image ?? '',
        row.sync,
        row.health,
        row.argoApp ?? '',
      ]
        .join(' ')
        .toLowerCase()

      return searchable.includes(query)
    })
  }, [driftFilter, rows, search])

  const summary = useMemo(() => {
    const driftCount = rows.filter((row) => row.drift).length
    const syncedCount = rows.filter((row) => row.sync === 'synced').length
    const degradedCount = rows.filter((row) => row.health === 'degraded').length

    return {
      total: rows.length,
      driftCount,
      syncedCount,
      degradedCount,
    }
  }, [rows])

  return (
    <PageShell
      title="Release Dashboard"
      description="Trace commits to deployed images and Argo CD sync state, with explicit drift visibility."
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-end gap-2">
          <LiveStatusBadge status={liveStatus} />
          <DataSourceBadge source={dataSource} />
        </div>
        {!isLoading && !error && warnings.length > 0 ? (
          <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
            <p className="text-sm font-medium text-amber-900 dark:text-amber-200">Partial data warning</p>
            <ul className="mt-1 space-y-1 text-xs text-amber-900 dark:text-amber-200">
              {warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {!isLoading && authDiagnostic ? (
          <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
            <p className="text-sm font-medium text-amber-900 dark:text-amber-200">Auth/session diagnostics</p>
            <p className="mt-1 text-xs text-amber-900 dark:text-amber-200">{authDiagnostic.summary}</p>
            <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-amber-900 dark:text-amber-200">
              {authDiagnostic.hints.map((hint) => (
                <li key={hint}>{hint}</li>
              ))}
            </ul>
            {authDiagnostic.responseUrl ? (
              <p className="mt-2 break-all text-xs text-amber-900 dark:text-amber-200">
                Gateway target: {authDiagnostic.responseUrl}
              </p>
            ) : null}
          </div>
        ) : null}
        <div className="grid gap-3 md:grid-cols-5">
          <SummaryCard label="Tracked releases" value={String(summary.total)} />
          <SummaryCard label="Synced" value={String(summary.syncedCount)} tone="text-emerald-700 dark:text-emerald-300" />
          <SummaryCard label="Drift" value={String(summary.driftCount)} tone="text-amber-700 dark:text-amber-300" />
          <SummaryCard label="Degraded" value={String(summary.degradedCount)} tone="text-rose-700 dark:text-rose-300" />
          <UnknownCountCard count={unknownFieldCount} source={dataSource} />
        </div>

        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]">
          <label className="space-y-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Search</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search service, SHA, image, or Argo app..."
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Drift Filter</span>
            <select
              value={driftFilter}
              onChange={(event) => setDriftFilter(event.target.value as DriftFilter)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              <option value="all">All releases</option>
              <option value="drift_only">Drift only</option>
            </select>
          </label>
        </div>

        {isLoading ? <LoadingState label="Loading release dashboard..." rows={5} /> : null}
        {!isLoading && error ? <ErrorState message={error} onRetry={() => void loadDashboard()} /> : null}
        {!isLoading && !error && rows.length === 0 ? (
          <EmptyState title="No release data available yet." description="Connect release metadata and service registry APIs." />
        ) : null}
        {!isLoading && !error && rows.length > 0 && filteredRows.length === 0 ? (
          <EmptyState title="No releases match your filters." description="Try a different query or include non-drifted releases." />
        ) : null}

        {!isLoading && !error && filteredRows.length > 0 ? (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="min-w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/30">
                  <th className="px-3 py-3 font-medium text-muted-foreground">Service</th>
                  <th className="px-3 py-3 font-medium text-muted-foreground">Commit</th>
                  <th className="px-3 py-3 font-medium text-muted-foreground">Image</th>
                  <th className="px-3 py-3 font-medium text-muted-foreground">Argo Sync</th>
                  <th className="px-3 py-3 font-medium text-muted-foreground">Drift</th>
                  <th className="px-3 py-3 font-medium text-muted-foreground">Deployed</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row) => (
                  <tr key={row.id} className="border-b border-border/70 align-top">
                    <td className="space-y-1 px-3 py-3">
                      <p className="font-medium">
                        <AppLink to={`/services/${encodeURIComponent(row.serviceId)}`} className="hover:underline">
                          {row.serviceName}
                        </AppLink>
                      </p>
                      <p className="text-xs text-muted-foreground">{row.environment}</p>
                    </td>
                    <td className="px-3 py-3">
                      {row.commitUrl && row.commitSha ? (
                        <a
                          href={row.commitUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="font-mono text-primary hover:underline"
                        >
                          {shortSha(row.commitSha)}
                        </a>
                      ) : (
                        row.commitSha ? (
                          <span className="font-mono text-muted-foreground">{shortSha(row.commitSha)}</span>
                        ) : (
                          <UnknownValue source={dataSource} />
                        )
                      )}
                      {row.desiredCommitSha && row.desiredCommitSha !== row.commitSha ? (
                        <p className="mt-1 font-mono text-xs text-amber-700 dark:text-amber-300">
                          desired: {shortSha(row.desiredCommitSha)}
                        </p>
                      ) : null}
                    </td>
                    <td className="px-3 py-3">
                      {row.imageUrl && row.image ? (
                        <a
                          href={row.imageUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="break-all text-primary hover:underline"
                        >
                          {row.imageTag ?? row.image}
                        </a>
                      ) : (
                        row.image || row.imageTag ? (
                          <span className="break-all text-muted-foreground">{row.imageTag ?? row.image}</span>
                        ) : (
                          <UnknownValue source={dataSource} />
                        )
                      )}
                      {row.desiredImage && row.desiredImage !== row.image ? (
                        <p className="mt-1 break-all text-xs text-amber-700 dark:text-amber-300">desired: {row.desiredImage}</p>
                      ) : null}
                    </td>
                    <td className="space-y-1 px-3 py-3">
                      <SyncBadge status={row.sync} />
                      <HealthBadge status={row.health} />
                      {row.argoApp ? (
                        row.argoAppUrl ? (
                          <p>
                            <a
                              href={row.argoAppUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="text-xs text-primary hover:underline"
                            >
                              {row.argoApp}
                            </a>
                          </p>
                        ) : (
                          <p className="text-xs text-muted-foreground">{row.argoApp}</p>
                        )
                      ) : (
                        <UnknownValue source={dataSource} />
                      )}
                    </td>
                    <td className="px-3 py-3">
                      <DriftBadge drift={row.drift} />
                    </td>
                    <td className="px-3 py-3 text-muted-foreground">
                      {row.deployedAt ? formatDate(row.deployedAt) : <UnknownValue source={dataSource} />}
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
