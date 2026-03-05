import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import {
  getPlatformHealthOverview,
  type IncidentSeverity,
  type IncidentStatus,
  type PlatformHealthOverview,
} from '@/lib/adapters/platform-health'
import { buildGrafanaDashboardUrl } from '@/lib/config'
import { cn } from '@/lib/utils'

interface SummaryCardProps {
  label: string
  value: string
  tone?: string
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

function SummaryCard({ label, value, tone }: SummaryCardProps) {
  return (
    <article className="rounded-md border border-border bg-background p-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn('mt-2 text-2xl font-semibold', tone)}>{value}</p>
    </article>
  )
}

function AlertLevelBadge({ level }: { level: 'none' | 'warning' | 'critical' }) {
  const tone =
    level === 'critical'
      ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
      : level === 'warning'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'

  const label = level === 'none' ? 'none' : level

  return <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium capitalize', tone)}>{label}</span>
}

function SeverityBadge({ severity }: { severity: IncidentSeverity }) {
  const tone =
    severity === 'critical'
      ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
      : severity === 'warning'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-sky-500/10 text-sky-700 dark:text-sky-300'

  return (
    <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium capitalize', tone)}>{severity}</span>
  )
}

function IncidentStatusBadge({ status }: { status: IncidentStatus }) {
  const tone =
    status === 'active'
      ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
      : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'

  return <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium capitalize', tone)}>{status}</span>
}

export function PlatformHealthPage() {
  const [overview, setOverview] = useState<PlatformHealthOverview | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  const loadOverview = useCallback(async () => {
    setIsLoading(true)
    setError('')

    try {
      const response = await getPlatformHealthOverview()
      setOverview(response)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load platform health overview'
      setError(message)
      setOverview(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadOverview()
  }, [loadOverview])

  const visibleIncidents = useMemo(() => overview?.incidents.slice(0, 12) ?? [], [overview])

  return (
    <PageShell
      title="Platform Health"
      description="Platform-wide status snapshot with degraded services and active incident feed."
    >
      <div className="space-y-4">
        {isLoading ? <LoadingState label="Loading platform health..." rows={5} /> : null}
        {!isLoading && error ? <ErrorState message={error} onRetry={() => void loadOverview()} /> : null}

        {!isLoading && !error && overview ? (
          <>
            <div className="grid gap-3 md:grid-cols-4">
              <SummaryCard label="Tracked Services" value={String(overview.summary.totalServices)} />
              <SummaryCard
                label="Degraded Services"
                value={String(overview.summary.degradedServices)}
                tone="text-amber-700 dark:text-amber-300"
              />
              <SummaryCard
                label="Active Alerts"
                value={String(overview.summary.activeAlerts)}
                tone="text-rose-700 dark:text-rose-300"
              />
              <SummaryCard
                label="Active Incidents"
                value={String(overview.summary.activeIncidents)}
                tone="text-amber-700 dark:text-amber-300"
              />
            </div>

            {overview.warnings.length > 0 ? (
              <div className="rounded-md border border-amber-400/40 bg-amber-500/10 p-3">
                <p className="text-sm font-medium text-amber-800 dark:text-amber-200">Partial monitoring data</p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-900 dark:text-amber-100">
                  {overview.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="grid gap-4 xl:grid-cols-2">
              <section className="rounded-md border border-border bg-background p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Unhealthy Services</h2>
                </div>

                {overview.unhealthyServices.length === 0 ? (
                  <EmptyState title="No unhealthy services currently detected." />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="min-w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-border">
                          <th className="px-2 py-2 font-medium text-muted-foreground">Service</th>
                          <th className="px-2 py-2 font-medium text-muted-foreground">Health</th>
                          <th className="px-2 py-2 font-medium text-muted-foreground">Sync</th>
                          <th className="px-2 py-2 font-medium text-muted-foreground">Alert</th>
                          <th className="px-2 py-2 font-medium text-muted-foreground">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {overview.unhealthyServices.map((service) => {
                          const grafanaUrl = buildGrafanaDashboardUrl(service.serviceId, '24h')

                          return (
                            <tr key={service.serviceId} className="border-b border-border/70 align-top">
                              <td className="px-2 py-2 font-medium">
                                <AppLink to={`/services/${encodeURIComponent(service.serviceId)}`} className="hover:underline">
                                  {service.serviceName}
                                </AppLink>
                                {service.alertReasons.length > 0 ? (
                                  <p className="mt-1 text-xs text-muted-foreground">{service.alertReasons[0]}</p>
                                ) : null}
                              </td>
                              <td className="px-2 py-2 capitalize text-muted-foreground">{service.health}</td>
                              <td className="px-2 py-2 capitalize text-muted-foreground">{service.sync.replace(/_/g, ' ')}</td>
                              <td className="px-2 py-2">
                                <AlertLevelBadge level={service.alertLevel} />
                              </td>
                              <td className="px-2 py-2">
                                {grafanaUrl ? (
                                  <a
                                    href={grafanaUrl}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="text-xs text-primary hover:underline"
                                  >
                                    Open in Grafana
                                  </a>
                                ) : (
                                  <span className="text-xs text-muted-foreground">Grafana link unavailable</span>
                                )}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              <section className="rounded-md border border-border bg-background p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Latest Alerts</h2>
                </div>

                {visibleIncidents.length === 0 ? (
                  <EmptyState title="No active alerts available." />
                ) : (
                  <ul className="space-y-2">
                    {visibleIncidents.map((incident) => {
                      const serviceUrl = incident.serviceId ? `/services/${encodeURIComponent(incident.serviceId)}` : ''
                      const grafanaUrl = incident.serviceId ? buildGrafanaDashboardUrl(incident.serviceId, '24h') : ''

                      return (
                        <li key={incident.id} className="rounded-md border border-border p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <SeverityBadge severity={incident.severity} />
                            <IncidentStatusBadge status={incident.status} />
                            <span className="text-xs text-muted-foreground">{formatDate(incident.startedAt)}</span>
                          </div>
                          <p className="mt-2 text-sm font-medium">{incident.title}</p>
                          {incident.description ? (
                            <p className="mt-1 text-xs text-muted-foreground">{incident.description}</p>
                          ) : null}
                          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
                            {incident.source ? <span className="text-muted-foreground">Source: {incident.source}</span> : null}
                            {serviceUrl ? <AppLink to={serviceUrl} className="text-primary hover:underline">Service details</AppLink> : null}
                            {grafanaUrl ? (
                              <a href={grafanaUrl} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                                Open in Grafana
                              </a>
                            ) : null}
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </section>
            </div>
          </>
        ) : null}
      </div>
    </PageShell>
  )
}
