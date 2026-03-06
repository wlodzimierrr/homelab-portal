import { useCallback, useEffect, useMemo, useState } from 'react'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { AppLink } from '@/components/navigation/app-link'
import {
  getCatalogReconciliation,
  getServiceRegistryDiagnostics,
  type CatalogJoinDiagnostics,
  type CatalogJoinRow,
  type ServiceRegistryDiagnosticsResponse,
} from '@/lib/api'
import { getDeploymentHistory } from '@/lib/adapters/deployments'
import { UptimeIndicator } from '@/components/uptime-indicator'
import { deriveServiceIdentity, getServicesRegistry, type ServiceRegistryItem } from '@/lib/adapters/services'
import { summarizeDeploymentAlerts, type DeploymentAlertLevel } from '@/lib/deployment-alerts'
import type { ServiceIncidentBadge } from '@/lib/incident-alerts'
import { cn } from '@/lib/utils'

type HealthStatus = 'healthy' | 'degraded' | 'unknown'
type SyncStatus = 'synced' | 'out_of_sync' | 'unknown'

interface ServiceRow extends ServiceRegistryItem {
  health: HealthStatus
  sync: SyncStatus
}

interface ServiceAlertState {
  level: DeploymentAlertLevel
  suspicious: boolean
}

interface ServicesPageProps {
  incidentServiceAlerts?: Record<string, ServiceIncidentBadge>
}

function projectAnchor(projectId: string, env: string) {
  return `${projectId}-${env}`.toLowerCase()
}

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

function formatLastDeploy(value?: string) {
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

function StatusBadge({ label, value }: { label: string; value: string }) {
  const normalized = value.toLowerCase()
  const tone =
    normalized === 'healthy' || normalized === 'synced'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : normalized === 'degraded' || normalized === 'out_of_sync'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-muted text-muted-foreground'

  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>
      {label}: {value}
    </span>
  )
}

function SourceStateBadge({ state }: { state?: string }) {
  const normalized = state?.toLowerCase() ?? 'unknown'
  const tone =
    normalized === 'fresh'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : normalized === 'stale'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : normalized === 'empty'
          ? 'bg-slate-500/10 text-slate-700 dark:text-slate-300'
          : 'bg-muted text-muted-foreground'

  return <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium capitalize', tone)}>{normalized}</span>
}

function ReconciliationBadge({ diagnostics }: { diagnostics?: CatalogJoinDiagnostics }) {
  const mismatchCount =
    (diagnostics?.projectOnlyCount ?? 0) +
    (diagnostics?.serviceOnlyCount ?? 0) +
    (diagnostics?.oneToManyCount ?? 0) +
    (diagnostics?.ambiguousJoinCount ?? 0)
  const tone =
    mismatchCount === 0
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : 'bg-amber-500/10 text-amber-700 dark:text-amber-300'

  return (
    <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium', tone)}>
      {mismatchCount === 0 ? 'Aligned' : `${mismatchCount} mismatch${mismatchCount === 1 ? '' : 'es'}`}
    </span>
  )
}

function SummaryCard({ label, value, meta }: { label: string; value: string; meta?: string }) {
  return (
    <article className="rounded-md border border-border bg-background p-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-2 text-2xl font-semibold">{value}</p>
      {meta ? <p className="mt-1 text-xs text-muted-foreground">{meta}</p> : null}
    </article>
  )
}

function IncidentCountBadge({ alert }: { alert: ServiceIncidentBadge }) {
  const severity = alert.highestSeverity ?? 'info'
  const tone =
    severity === 'critical'
      ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
      : severity === 'warning'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-sky-500/10 text-sky-700 dark:text-sky-300'

  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>
      Alerts: {alert.total}
    </span>
  )
}

export function ServicesPage({ incidentServiceAlerts = {} }: ServicesPageProps) {
  const [services, setServices] = useState<ServiceRow[]>([])
  const [catalogRows, setCatalogRows] = useState<CatalogJoinRow[]>([])
  const [diagnostics, setDiagnostics] = useState<ServiceRegistryDiagnosticsResponse | null>(null)
  const [serviceAlerts, setServiceAlerts] = useState<Record<string, ServiceAlertState>>({})
  const [warnings, setWarnings] = useState<string[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [environmentFilter, setEnvironmentFilter] = useState('all')

  const loadServices = useCallback(async () => {
    setIsLoading(true)
    setError('')
    setWarnings([])
    try {
      const [servicesResult, catalogResult, diagnosticsResult] = await Promise.allSettled([
        getServicesRegistry(),
        getCatalogReconciliation(),
        getServiceRegistryDiagnostics(),
      ])
      if (servicesResult.status !== 'fulfilled') {
        throw servicesResult.reason
      }

      const response = servicesResult.value
      const nextWarnings: string[] = []
      setServices(response)
      setCatalogRows(catalogResult.status === 'fulfilled' ? catalogResult.value.rows : [])
      if (catalogResult.status !== 'fulfilled') {
        nextWarnings.push('Catalog reconciliation is unavailable; project links may be incomplete.')
      }
      if (diagnosticsResult.status === 'fulfilled') {
        setDiagnostics(diagnosticsResult.value)
        if (diagnosticsResult.value.freshness.state === 'stale') {
          nextWarnings.push('Service registry data is stale; rerun cluster sync to refresh the live catalog.')
        }
        if (diagnosticsResult.value.freshness.state === 'empty') {
          nextWarnings.push('Service registry is reachable but returned zero live services.')
        }
        if (
          diagnosticsResult.value.joinMismatch.ciUnmatchedCount > 0 ||
          diagnosticsResult.value.joinMismatch.argoUnmatchedCount > 0
        ) {
          nextWarnings.push('Release metadata has unmatched CI or Argo rows; some service status links may be incomplete.')
        }
      } else {
        setDiagnostics(null)
        nextWarnings.push('Service freshness diagnostics are unavailable.')
      }
      const alerts = await Promise.all(
        response.map(async (service) => {
          try {
            const identity = deriveServiceIdentity(service)
            const deployments = await getDeploymentHistory(identity, { limit: 3 })
            const summary = summarizeDeploymentAlerts(
              deployments.map((item) => ({
                outcome: item.outcome,
                regressionScore: item.regressionScore,
                errorRateDeltaPct: item.errorRatePct.delta,
                latencyDeltaMs: item.p95LatencyMs.delta,
                availabilityDeltaPct: item.availabilityPct.delta,
              })),
            )
            return [service.id, { suspicious: summary.suspicious, level: summary.level }] as const
          } catch {
            return [service.id, { suspicious: false, level: 'none' as const }] as const
          }
        }),
      )
      setServiceAlerts(Object.fromEntries(alerts))
      setWarnings(nextWarnings)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load services'
      setError(message)
      setDiagnostics(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadServices()
  }, [loadServices])

  const environmentOptions = useMemo(() => {
    const unique = new Set<string>()
    for (const service of services) {
      for (const env of service.environments) {
        unique.add(env)
      }
    }
    return [...unique].sort((a, b) => a.localeCompare(b))
  }, [services])

  const filteredServices = useMemo(() => {
    const query = search.trim().toLowerCase()

    return services.filter((service) => {
      const matchesEnvironment =
        environmentFilter === 'all' || service.environments.includes(environmentFilter)

      if (!matchesEnvironment) {
        return false
      }

      if (!query) {
        return true
      }

      const searchable = [
        service.name,
        service.publicUrl ?? '',
        service.environments.join(' '),
        service.health,
        service.sync,
      ]
        .join(' ')
        .toLowerCase()

      return searchable.includes(query)
    })
  }, [environmentFilter, search, services])
  const projectByServiceKey = useMemo(() => {
    const map = new Map<string, CatalogJoinRow>()
    for (const row of catalogRows) {
      for (const serviceId of row.serviceIds) {
        map.set(`${serviceId}:${row.env}`, row)
      }
      if (row.primaryServiceId) {
        map.set(`${row.primaryServiceId}:${row.env}`, row)
      }
    }
    return map
  }, [catalogRows])
  const mismatchCount =
    (diagnostics?.catalogJoin.projectOnlyCount ?? 0) +
    (diagnostics?.catalogJoin.serviceOnlyCount ?? 0) +
    (diagnostics?.catalogJoin.oneToManyCount ?? 0) +
    (diagnostics?.catalogJoin.ambiguousJoinCount ?? 0)

  return (
    <PageShell
      title="Services"
      description="Read-only service catalog with environments, runtime status, and external links."
    >
      {!isLoading ? (
        <div className="mb-4 grid gap-3 md:grid-cols-3">
          <SummaryCard
            label="Catalog source"
            value={diagnostics?.freshness.state === 'fresh' ? 'Live' : diagnostics?.freshness.state === 'stale' ? 'Stale' : diagnostics?.freshness.state === 'empty' ? 'Empty' : 'Unknown'}
            meta={`Last sync: ${formatTimestamp(diagnostics?.freshness.lastSyncedAt)}`}
          />
          <SummaryCard
            label="Services tracked"
            value={String(services.length)}
            meta={diagnostics ? `Rows in registry: ${diagnostics.freshness.rowCount}` : 'Diagnostics unavailable'}
          />
          <SummaryCard
            label="Join drift"
            value={String(mismatchCount)}
            meta={
              diagnostics?.joinMismatch.ciUnmatchedCount || diagnostics?.joinMismatch.argoUnmatchedCount
                ? 'Release metadata has unmatched rows.'
                : mismatchCount === 0
                  ? 'Project and service joins are aligned.'
                  : 'Catalog reconciliation needs review.'
            }
          />
        </div>
      ) : null}

      {!isLoading && diagnostics ? (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-border bg-background p-3 text-sm">
          <span className="text-muted-foreground">Source state</span>
          <SourceStateBadge state={diagnostics.freshness.state} />
          <span className="ml-2 text-muted-foreground">Reconciliation</span>
          <ReconciliationBadge diagnostics={diagnostics.catalogJoin} />
        </div>
      ) : null}

      {!isLoading && warnings.length > 0 ? (
        <div className="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
          <p className="text-sm font-medium text-amber-900 dark:text-amber-200">Partial service readiness</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-900 dark:text-amber-200">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mb-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]">
        <label className="space-y-1">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Search</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search service, env, or status..."
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Environment</span>
          <select
            value={environmentFilter}
            onChange={(event) => setEnvironmentFilter(event.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="all">All environments</option>
            {environmentOptions.map((env) => (
              <option key={env} value={env}>
                {env}
              </option>
            ))}
          </select>
        </label>
      </div>

      {isLoading ? <LoadingState label="Loading services..." rows={4} /> : null}
      {!isLoading && error ? <ErrorState message={error} onRetry={() => void loadServices()} /> : null}
      {!isLoading && !error && services.length === 0 ? (
        <EmptyState title="No services available yet." />
      ) : null}
      {!isLoading && !error && services.length > 0 && filteredServices.length === 0 ? (
        <EmptyState title="No services match the current filters." description="Try a different search or environment." />
      ) : null}
      {!isLoading && !error && filteredServices.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="px-3 py-3 font-medium text-muted-foreground">Service</th>
                <th className="px-3 py-3 font-medium text-muted-foreground">Environment(s)</th>
                <th className="px-3 py-3 font-medium text-muted-foreground">Status</th>
                <th className="px-3 py-3 font-medium text-muted-foreground">Project</th>
                <th className="px-3 py-3 font-medium text-muted-foreground">Public URL</th>
                <th className="px-3 py-3 font-medium text-muted-foreground">Last Deploy</th>
              </tr>
            </thead>
            <tbody>
              {filteredServices.map((service) => (
                <tr key={service.id} className="border-b border-border/70 align-top">
                  <td className="px-3 py-3 font-medium">
                    <AppLink to={`/services/${encodeURIComponent(service.id)}`} className="hover:underline">
                      {service.name}
                    </AppLink>
                    {incidentServiceAlerts[service.id]?.total ? (
                      <div className="mt-2">
                        <IncidentCountBadge alert={incidentServiceAlerts[service.id]} />
                      </div>
                    ) : null}
                  </td>
                  <td className="px-3 py-3 text-muted-foreground">{service.environments.join(', ')}</td>
                  <td className="px-3 py-3">
                    <div className="mb-2 flex flex-wrap gap-2">
                      <StatusBadge
                        label="Health"
                        value={
                          service.health === 'degraded' || serviceAlerts[service.id]?.suspicious
                            ? 'degraded'
                            : service.health
                        }
                      />
                      <StatusBadge label="Sync" value={service.sync} />
                      {serviceAlerts[service.id]?.suspicious ? (
                        <StatusBadge label="Alert" value="degraded" />
                      ) : null}
                    </div>
                    <UptimeIndicator
                      className="p-3"
                      uptime24h={service.uptime24hPct}
                      uptime7d={service.uptime7dPct}
                      lastRefreshedAt={service.metricsLastRefreshedAt}
                    />
                  </td>
                  <td className="px-3 py-3 text-muted-foreground">
                    {(() => {
                      const matchingRow = service.environments
                        .map((env) => projectByServiceKey.get(`${service.id}:${env}`))
                        .find((row) => row)
                      if (!matchingRow) {
                        return 'Unmatched'
                      }
                      return (
                        <AppLink
                          to={`/projects#${encodeURIComponent(projectAnchor(matchingRow.projectId, matchingRow.env))}`}
                          className="text-primary hover:underline"
                        >
                          {matchingRow.projectName}
                        </AppLink>
                      )
                    })()}
                  </td>
                  <td className="px-3 py-3">
                    {service.publicUrl ? (
                      <a
                        href={service.publicUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="break-all text-primary hover:underline"
                      >
                        {service.publicUrl}
                      </a>
                    ) : (
                      <span className="text-muted-foreground">N/A</span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-muted-foreground">{formatLastDeploy(service.lastDeployAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </PageShell>
  )
}
