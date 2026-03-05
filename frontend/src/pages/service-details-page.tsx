import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { ErrorState } from '@/components/error-state'
import { GrafanaEmbedPanel } from '@/components/grafana-embed-panel'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { ServiceHealthTimeline } from '@/components/service-health-timeline'
import { ServiceMetricCard, type MetricSeverity } from '@/components/service-metric-card'
import { StatusCard } from '@/components/status-card'
import { UptimeIndicator } from '@/components/uptime-indicator'
import { Button } from '@/components/ui/button'
import {
  getProjects,
  getService,
  getServiceDeployments,
  type Project,
  type ServiceDeployment,
  type ServiceEndpoint,
} from '@/lib/api'
import {
  createEmptyServiceMetricsSummary,
  getServiceMetricsSummary,
  type ServiceMetricsRange,
  type ServiceMetricsSummary,
} from '@/lib/adapters/service-metrics'
import {
  getServiceLogsQuickView,
  type LogsQuickViewPreset,
  type LogsQuickViewRange,
  type ServiceLogsQuickView,
} from '@/lib/adapters/logs-quickview'
import { getServiceIdentity } from '@/lib/adapters/services'
import {
  getServiceHealthTimeline,
  type ServiceHealthTimeline as ServiceHealthTimelineData,
  type TimelineWindow,
} from '@/lib/adapters/service-health-timeline'
import { summarizeDeploymentAlerts } from '@/lib/deployment-alerts'
import type { ServiceIncidentBadge } from '@/lib/incident-alerts'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'
import {
  buildArgoAppUrl,
  buildGrafanaDashboardUrl,
  buildGrafanaErrorPanelUrl,
  buildGrafanaLatencyPanelUrl,
  buildLogsUrl,
  config,
  isLogsConfigured,
} from '@/lib/config'

interface ServiceDetailsPageProps {
  serviceId: string
  incidentServiceAlerts?: Record<string, ServiceIncidentBadge>
}

type HealthStatus = 'healthy' | 'degraded' | 'unknown'
type SyncStatus = 'synced' | 'out_of_sync' | 'unknown'

interface ServiceOverviewData {
  id: string
  name: string
  version: string
  health: HealthStatus
  sync: SyncStatus
  endpoints: ServiceEndpoint[]
  deployments: ServiceDeployment[]
}

interface QuickLinkCardProps {
  label: string
  description: string
  href?: string
}

interface LogsPreset {
  id: LogsQuickViewPreset
  label: string
  description: string
  queryTemplate: string
}

function normalizeHealthStatus(value?: string): HealthStatus {
  if (!value) {
    return 'unknown'
  }
  const normalized = value.trim().toLowerCase()
  if (normalized === 'healthy') {
    return 'healthy'
  }
  if (normalized === 'degraded' || normalized === 'unhealthy') {
    return 'degraded'
  }
  return 'unknown'
}

function normalizeSyncStatus(value?: string): SyncStatus {
  if (!value) {
    return 'unknown'
  }
  const normalized = value.trim().toLowerCase()
  if (normalized === 'synced') {
    return 'synced'
  }
  if (normalized === 'out_of_sync' || normalized === 'out-of-sync' || normalized === 'outofsync') {
    return 'out_of_sync'
  }
  return 'unknown'
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

function safeDecodeServiceId(rawServiceId: string) {
  try {
    return decodeURIComponent(rawServiceId)
  } catch {
    return rawServiceId
  }
}

function createMockDeployments(serviceId: string): ServiceDeployment[] {
  const now = Date.now()
  return [0, 1, 2, 3, 4].map((offset) => ({
    id: `${serviceId}-mock-${offset + 1}`,
    version: `v0.0.${9 - offset}`,
    status: offset === 2 ? 'degraded' : 'succeeded',
    deployedAt: new Date(now - offset * 1000 * 60 * 60 * 24).toISOString(),
  }))
}

function getMetricSeverity(
  value: number | undefined,
  thresholds: { warning: number; critical: number; direction: 'higher_is_better' | 'lower_is_better' },
): MetricSeverity {
  if (typeof value !== 'number') {
    return 'unknown'
  }

  if (thresholds.direction === 'higher_is_better') {
    if (value < thresholds.critical) {
      return 'critical'
    }
    if (value < thresholds.warning) {
      return 'warning'
    }
    return 'healthy'
  }

  if (value > thresholds.critical) {
    return 'critical'
  }
  if (value > thresholds.warning) {
    return 'warning'
  }
  return 'healthy'
}

function buildFromProjects(serviceId: string, projects: Project[]): ServiceOverviewData {
  const matches = projects.filter((project) => project.name.trim().toLowerCase() === serviceId.toLowerCase())
  const primary = matches[0]

  const endpointMap = new Map<string, ServiceEndpoint>()
  for (const project of matches) {
    if (project.publicUrl) {
      endpointMap.set(project.publicUrl, {
        type: 'public',
        label: `${project.environment} public`,
        url: project.publicUrl,
      })
    }
    if (project.internalUrl) {
      endpointMap.set(project.internalUrl, {
        type: 'internal',
        label: `${project.environment} internal`,
        url: project.internalUrl,
      })
    }
  }

  return {
    id: serviceId,
    name: primary?.name ?? serviceId,
    version: 'N/A',
    health: normalizeHealthStatus(primary?.health),
    sync: normalizeSyncStatus(primary?.sync),
    endpoints: [...endpointMap.values()],
    deployments: [],
  }
}

function buildEndpointList(
  endpoints: ServiceEndpoint[] | undefined,
  publicUrl: string | undefined,
  internalUrls: string[] | undefined,
) {
  const collected: ServiceEndpoint[] = []

  if (endpoints?.length) {
    for (const endpoint of endpoints) {
      if (endpoint.url) {
        collected.push(endpoint)
      }
    }
  }

  if (publicUrl) {
    collected.push({
      type: 'public',
      label: 'Public URL',
      url: publicUrl,
    })
  }

  for (const internalUrl of internalUrls ?? []) {
    if (internalUrl) {
      collected.push({
        type: 'internal',
        label: 'Internal URL',
        url: internalUrl,
      })
    }
  }

  return collected.filter(
    (endpoint, index, source) => source.findIndex((item) => item.url === endpoint.url) === index,
  )
}

function QuickLinkCard({ label, description, href }: QuickLinkCardProps) {
  if (!href || href.trim() === '') {
    return (
      <div className="rounded-md border border-border bg-background p-3">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
        <p className="mt-2 text-xs text-muted-foreground">Unavailable due to missing monitoring URL configuration.</p>
      </div>
    )
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="rounded-md border border-border bg-background p-3 transition-colors hover:bg-accent"
    >
      <p className="text-sm font-medium">{label}</p>
      <p className="text-xs text-muted-foreground">{description}</p>
    </a>
  )
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

const logsPresets: LogsPreset[] = [
  {
    id: 'errors',
    label: 'Errors',
    description: 'HTTP 5xx or error-level logs',
    queryTemplate: '{namespace="{{namespace}}", app="{{app_label}}"} |= "error" or |= " 5" ',
  },
  {
    id: 'restarts',
    label: 'Restarts',
    description: 'Container restart signals and crash loops',
    queryTemplate: '{namespace="{{namespace}}", app="{{app_label}}"} |= "restart" or |= "CrashLoopBackOff"',
  },
  {
    id: 'warnings',
    label: 'Warnings',
    description: 'Recent warning/timeout style signals',
    queryTemplate: '{namespace="{{namespace}}", app="{{app_label}}"} |= "warn" or |= "timeout"',
  },
]

const logsRangeOptions: Array<{ value: LogsQuickViewRange; label: string }> = [
  { value: '15m', label: '15m' },
  { value: '1h', label: '1h' },
  { value: '6h', label: '6h' },
  { value: '24h', label: '24h' },
]

export function ServiceDetailsPage({ serviceId, incidentServiceAlerts = {} }: ServiceDetailsPageProps) {
  const decodedServiceId = useMemo(() => safeDecodeServiceId(serviceId), [serviceId])
  const [serviceIdentity, setServiceIdentity] = useState<ServiceIdentity>(() =>
    createServiceIdentity({ serviceId: decodedServiceId }),
  )
  const [overview, setOverview] = useState<ServiceOverviewData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [metricsRange, setMetricsRange] = useState<ServiceMetricsRange>('24h')
  const [metrics, setMetrics] = useState<ServiceMetricsSummary>(() =>
    createEmptyServiceMetricsSummary(decodedServiceId, '24h'),
  )
  const [metricsLoading, setMetricsLoading] = useState(true)
  const [metricsError, setMetricsError] = useState('')
  const [timelineWindow, setTimelineWindow] = useState<TimelineWindow>('24h')
  const [timeline, setTimeline] = useState<ServiceHealthTimelineData | null>(null)
  const [timelineLoading, setTimelineLoading] = useState(true)
  const [timelineError, setTimelineError] = useState('')
  const [logsDrawerOpen, setLogsDrawerOpen] = useState(false)
  const [activeLogsPreset, setActiveLogsPreset] = useState<LogsQuickViewPreset>('errors')
  const [logsRange, setLogsRange] = useState<LogsQuickViewRange>('1h')
  const [logsResult, setLogsResult] = useState<ServiceLogsQuickView | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)
  const [logsError, setLogsError] = useState('')

  const loadOverview = useCallback(async () => {
    setIsLoading(true)
    setError('')

    try {
      const identity = await getServiceIdentity(decodedServiceId).catch(() =>
        createServiceIdentity({ serviceId: decodedServiceId }),
      )
      setServiceIdentity(identity)

      const [serviceResult, projectsResult, deploymentsResult] = await Promise.allSettled([
        getService(decodedServiceId),
        getProjects(),
        getServiceDeployments(decodedServiceId),
      ])

      const fallback =
        projectsResult.status === 'fulfilled'
          ? buildFromProjects(decodedServiceId, projectsResult.value.projects)
          : buildFromProjects(decodedServiceId, [])

      const finalOverview: ServiceOverviewData =
        serviceResult.status === 'fulfilled'
          ? {
              id: serviceResult.value.id || decodedServiceId,
              name: serviceResult.value.name || decodedServiceId,
              version: serviceResult.value.version ?? 'N/A',
              health: normalizeHealthStatus(serviceResult.value.health),
              sync: normalizeSyncStatus(serviceResult.value.sync),
              endpoints: buildEndpointList(
                serviceResult.value.endpoints,
                serviceResult.value.publicUrl,
                serviceResult.value.internalUrls,
              ),
              deployments: serviceResult.value.deployments ?? [],
            }
          : fallback

      if (finalOverview.endpoints.length === 0) {
        finalOverview.endpoints = fallback.endpoints
      }

      if (deploymentsResult.status === 'fulfilled' && deploymentsResult.value.deployments.length > 0) {
        finalOverview.deployments = deploymentsResult.value.deployments
      }

      if (finalOverview.deployments.length === 0) {
        finalOverview.deployments = createMockDeployments(decodedServiceId)
      }

      setOverview(finalOverview)
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load service overview'
      setError(message)
    } finally {
      setIsLoading(false)
    }
  }, [decodedServiceId])

  useEffect(() => {
    void loadOverview()
  }, [loadOverview])

  const loadMetrics = useCallback(async () => {
    setMetricsLoading(true)
    setMetricsError('')

    try {
      const response = await getServiceMetricsSummary(serviceIdentity, metricsRange)
      setMetrics(response)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load service metrics'
      setMetricsError(message)
      setMetrics(createEmptyServiceMetricsSummary(serviceIdentity, metricsRange))
    } finally {
      setMetricsLoading(false)
    }
  }, [metricsRange, serviceIdentity])

  useEffect(() => {
    void loadMetrics()
  }, [loadMetrics])

  const loadTimeline = useCallback(async () => {
    setTimelineLoading(true)
    setTimelineError('')

    try {
      const response = await getServiceHealthTimeline(serviceIdentity, timelineWindow)
      setTimeline(response)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load service timeline'
      setTimelineError(message)
    } finally {
      setTimelineLoading(false)
    }
  }, [serviceIdentity, timelineWindow])

  useEffect(() => {
    void loadTimeline()
  }, [loadTimeline])

  const argoUrl = useMemo(() => buildArgoAppUrl(decodedServiceId), [decodedServiceId])
  const grafanaTimeRange = '6h'
  const grafanaUrl = useMemo(
    () => buildGrafanaDashboardUrl(decodedServiceId, grafanaTimeRange),
    [decodedServiceId],
  )
  const latencyPanelUrl = useMemo(
    () => buildGrafanaLatencyPanelUrl(decodedServiceId, grafanaTimeRange),
    [decodedServiceId],
  )
  const errorPanelUrl = useMemo(
    () => buildGrafanaErrorPanelUrl(decodedServiceId, grafanaTimeRange),
    [decodedServiceId],
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
  const logsConfigured = isLogsConfigured()
  const logsNamespace = serviceIdentity.namespace || 'default'
  const logsAppLabel = serviceIdentity.appLabel || decodedServiceId
  const presetLinks = useMemo(() => {
    return logsPresets.map((preset) => ({
      ...preset,
      query: preset.queryTemplate
        .replaceAll('{{namespace}}', logsNamespace)
        .replaceAll('{{app_label}}', logsAppLabel),
      href: buildLogsUrl({
        serviceId: decodedServiceId,
        namespace: logsNamespace,
        appLabel: logsAppLabel,
        timeRange: logsRange,
        preset: preset.id,
        query: preset.queryTemplate
          .replaceAll('{{namespace}}', logsNamespace)
          .replaceAll('{{app_label}}', logsAppLabel),
      }),
    }))
  }, [decodedServiceId, logsAppLabel, logsNamespace, logsRange])
  const activePreset = useMemo(
    () => presetLinks.find((preset) => preset.id === activeLogsPreset) ?? presetLinks[0],
    [activeLogsPreset, presetLinks],
  )
  const logsUrl = activePreset?.href ?? ''

  const loadQuickViewLogs = useCallback(async () => {
    setLogsLoading(true)
    setLogsError('')

    try {
      const response = await getServiceLogsQuickView(serviceIdentity, {
        preset: activeLogsPreset,
        range: logsRange,
      })
      setLogsResult(response)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load logs quick view'
      setLogsError(message)
      setLogsResult(null)
    } finally {
      setLogsLoading(false)
    }
  }, [activeLogsPreset, logsRange, serviceIdentity])

  useEffect(() => {
    if (!logsDrawerOpen || !logsConfigured) {
      return
    }
    void loadQuickViewLogs()
  }, [loadQuickViewLogs, logsConfigured, logsDrawerOpen])

  return (
    <PageShell
      title={`Service: ${decodedServiceId || 'unknown'}`}
      description="Overview for deployment status, endpoints, and recent release activity."
    >
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
          <div className="flex items-center gap-2">
            <span className="rounded-md bg-primary/10 px-2 py-1 text-xs font-medium text-primary">
              Overview
            </span>
            {incidentAlert?.total ? <IncidentServiceBadge alert={incidentAlert} /> : null}
          </div>
          <Button asChild variant="outline">
            <AppLink to={`/services/${encodeURIComponent(decodedServiceId)}/deployments`}>
              View deployments
            </AppLink>
          </Button>
        </div>

        {isLoading ? <LoadingState label="Loading service overview..." rows={4} /> : null}

        {!isLoading && error ? <ErrorState message={error} onRetry={() => void loadOverview()} /> : null}

        {!isLoading && !error && overview ? (
          <>
            <div className="grid gap-3 md:grid-cols-2">
              <article className="rounded-md border border-border bg-background p-4">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Deployed Version</p>
                <p className="mt-2 text-xl font-semibold">{overview.version}</p>
                <p className="mt-1 text-xs text-muted-foreground">Placeholder until deployment metadata API lands</p>
              </article>
              <StatusCard health={effectiveHealth} sync={overview.sync} />
            </div>

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

            {import.meta.env.DEV ? (
              <section className="space-y-3">
                <h2 className="text-sm font-semibold">Status Visual Checks</h2>
                <div className="grid gap-3 md:grid-cols-3">
                  <StatusCard health="healthy" sync="synced" />
                  <StatusCard health="degraded" sync="out_of_sync" />
                  <StatusCard />
                </div>
              </section>
            ) : null}

            <section className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">Service Metrics</h2>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  Range
                  <select
                    value={metricsRange}
                    onChange={(event) => setMetricsRange(event.target.value as ServiceMetricsRange)}
                    className="rounded-md border border-border bg-background px-2 py-1 text-xs"
                  >
                    <option value="1h">1h</option>
                    <option value="24h">24h</option>
                    <option value="7d">7d</option>
                  </select>
                </label>
              </div>
              <p className="text-xs text-muted-foreground">
                Live summary metrics from <code>/api/services/:serviceId/metrics/summary</code>.
              </p>
              {metricsError ? (
                <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
                  <p className="text-xs text-amber-900 dark:text-amber-200">{metricsError}</p>
                  <Button type="button" size="sm" variant="outline" className="mt-2" onClick={() => void loadMetrics()}>
                    Retry metrics
                  </Button>
                </div>
              ) : null}
              <UptimeIndicator
                uptime24h={metricsRange === '7d' ? undefined : metrics.uptimePct}
                uptime7d={metricsRange === '7d' ? metrics.uptimePct : undefined}
                lastRefreshedAt={metrics.generatedAt}
                isLoading={metricsLoading}
              />
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <ServiceMetricCard
                  label={`Uptime (${metricsRange})`}
                  value={metrics.uptimePct}
                  formatValue={(value) => `${value.toFixed(2)}%`}
                  lastRefreshedAt={metrics.generatedAt}
                  noData={metrics.noData.uptimePct}
                  isLoading={metricsLoading}
                  staleAfterMinutes={config.metricsStaleAfterMinutes}
                  severity={getMetricSeverity(metrics.uptimePct, {
                    warning: 99.9,
                    critical: 99.0,
                    direction: 'higher_is_better',
                  })}
                />
                <ServiceMetricCard
                  label="P95 Latency"
                  value={metrics.p95LatencyMs}
                  formatValue={(value) => `${Math.round(value)} ms`}
                  lastRefreshedAt={metrics.generatedAt}
                  noData={metrics.noData.p95LatencyMs}
                  isLoading={metricsLoading}
                  staleAfterMinutes={config.metricsStaleAfterMinutes}
                  severity={getMetricSeverity(metrics.p95LatencyMs, {
                    warning: 250,
                    critical: 500,
                    direction: 'lower_is_better',
                  })}
                />
                <ServiceMetricCard
                  label="Error Rate"
                  value={metrics.errorRatePct}
                  formatValue={(value) => `${value.toFixed(2)}%`}
                  lastRefreshedAt={metrics.generatedAt}
                  noData={metrics.noData.errorRatePct}
                  isLoading={metricsLoading}
                  staleAfterMinutes={config.metricsStaleAfterMinutes}
                  severity={getMetricSeverity(metrics.errorRatePct, {
                    warning: 1,
                    critical: 3,
                    direction: 'lower_is_better',
                  })}
                />
                <ServiceMetricCard
                  label="Restart Count"
                  value={metrics.restartCount}
                  formatValue={(value) => String(Math.round(value))}
                  lastRefreshedAt={metrics.generatedAt}
                  noData={metrics.noData.restartCount}
                  isLoading={metricsLoading}
                  staleAfterMinutes={config.metricsStaleAfterMinutes}
                  severity={getMetricSeverity(metrics.restartCount, {
                    warning: 1,
                    critical: 3,
                    direction: 'lower_is_better',
                  })}
                />
              </div>
            </section>

            <section className="space-y-3">
              <h2 className="text-sm font-semibold">Latency & Error Trends</h2>
              <p className="text-xs text-muted-foreground">
                Embedded Grafana panels scoped to this service and the last {grafanaTimeRange}.
              </p>
              <div className="grid gap-3 xl:grid-cols-2">
                <GrafanaEmbedPanel
                  key={`latency-${decodedServiceId}`}
                  title="P95 Latency Trend"
                  description="Latency trend panel for current service scope."
                  embedUrl={latencyPanelUrl}
                  dashboardUrl={grafanaUrl}
                  height={280}
                />
                <GrafanaEmbedPanel
                  key={`errors-${decodedServiceId}`}
                  title="Error Rate Trend"
                  description="Error-rate trend panel for current service scope."
                  embedUrl={errorPanelUrl}
                  dashboardUrl={grafanaUrl}
                  height={280}
                />
              </div>
            </section>

            <section className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">Service Health Timeline</h2>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  Window
                  <select
                    value={timelineWindow}
                    onChange={(event) => setTimelineWindow(event.target.value as TimelineWindow)}
                    className="rounded-md border border-border bg-background px-2 py-1 text-xs"
                  >
                    <option value="6h">6h</option>
                    <option value="24h">24h</option>
                    <option value="7d">7d</option>
                  </select>
                </label>
              </div>
              <p className="text-xs text-muted-foreground">
                Status-over-time timeline for healthy, degraded, and down transitions.
              </p>
              {timelineError ? (
                <ErrorState message={timelineError} onRetry={() => void loadTimeline()} />
              ) : (
                <ServiceHealthTimeline
                  segments={timeline?.segments ?? []}
                  lastRefreshedAt={timeline?.lastRefreshedAt}
                  isLoading={timelineLoading}
                />
              )}
            </section>

            <section className="space-y-3">
              <h2 className="text-sm font-semibold">Quick Links</h2>
              <div className="grid gap-3 md:grid-cols-3">
                <QuickLinkCard
                  label="Argo CD Application"
                  description="Open the GitOps application state"
                  href={argoUrl}
                />
                <QuickLinkCard
                  label="Grafana Dashboard"
                  description="Open service metrics dashboard"
                  href={grafanaUrl}
                />
                <div className="rounded-md border border-border bg-background p-3">
                  <p className="text-sm font-medium">Logs</p>
                  <p className="mb-3 text-xs text-muted-foreground">
                    Opens Grafana/Loki filtered by namespace, app label, and time range.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {logsConfigured ? (
                      <Button type="button" size="sm" variant="outline" onClick={() => setLogsDrawerOpen((open) => !open)}>
                        {logsDrawerOpen ? 'Hide logs panel' : 'View logs'}
                      </Button>
                    ) : (
                      <Button type="button" size="sm" disabled>
                        Logs unavailable
                      </Button>
                    )}
                    {logsConfigured ? (
                      <Button asChild size="sm">
                        <a href={logsUrl} target="_blank" rel="noreferrer">
                          Open full logs
                        </a>
                      </Button>
                    ) : null}
                  </div>
                </div>
              </div>
            </section>

            {logsDrawerOpen && logsConfigured ? (
              <section className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 className="text-sm font-semibold">Logs Quick View</h2>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    Range
                    <select
                      value={logsRange}
                      onChange={(event) => setLogsRange(event.target.value as LogsQuickViewRange)}
                      className="rounded-md border border-border bg-background px-2 py-1 text-xs"
                    >
                      {logsRangeOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <p className="text-xs text-muted-foreground">
                  Live Loki quick-view lines from <code>/api/services/:serviceId/logs/quickview</code>.
                </p>
                <div className="rounded-md border border-border bg-background p-3">
                  <div className="mb-3 flex flex-wrap gap-2">
                    {presetLinks.map((preset) => (
                      <Button
                        key={preset.id}
                        type="button"
                        size="sm"
                        variant={activeLogsPreset === preset.id ? 'default' : 'outline'}
                        onClick={() => setActiveLogsPreset(preset.id)}
                      >
                        {preset.label}
                      </Button>
                    ))}
                  </div>
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-border/70 bg-muted/20 p-3">
                    <div>
                      <p className="text-sm font-medium">{activePreset?.label ?? 'Preset'}</p>
                      <p className="text-xs text-muted-foreground">{activePreset?.description}</p>
                    </div>
                    {activePreset?.href ? (
                      <Button asChild size="sm">
                        <a href={activePreset.href} target="_blank" rel="noreferrer">
                          Open full logs
                        </a>
                      </Button>
                    ) : (
                      <Button type="button" size="sm" disabled>
                        Open full logs
                      </Button>
                    )}
                  </div>
                  {logsLoading ? <LoadingState label="Loading logs..." rows={3} /> : null}
                  {!logsLoading && logsError ? (
                    <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
                      <p className="text-xs text-amber-900 dark:text-amber-200">{logsError}</p>
                      <Button type="button" size="sm" variant="outline" className="mt-2" onClick={() => void loadQuickViewLogs()}>
                        Retry logs
                      </Button>
                    </div>
                  ) : null}
                  {!logsLoading && !logsError && (logsResult?.lines.length ?? 0) === 0 ? (
                    <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
                      No logs found for this preset and range.
                    </p>
                  ) : null}
                  {!logsLoading && !logsError && (logsResult?.lines.length ?? 0) > 0 ? (
                    <div className="space-y-2">
                      <div className="text-xs text-muted-foreground">
                        Returned {logsResult?.returned ?? 0} line(s)
                        {logsResult?.moreAvailable ? '; more logs are available.' : '.'}
                      </div>
                      <div className="max-h-80 space-y-2 overflow-y-auto rounded-md border border-border/70 bg-muted/10 p-2">
                        {logsResult?.lines.map((line) => (
                          <article key={`${line.timestamp}-${line.message.slice(0, 40)}`} className="rounded border border-border/60 bg-background p-2">
                            <p className="text-[11px] text-muted-foreground">{formatDate(line.timestamp)}</p>
                            <p className="mt-1 break-words font-mono text-xs">{line.message}</p>
                          </article>
                        ))}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Last refreshed: {formatDate(logsResult?.generatedAt)}
                      </p>
                    </div>
                  ) : null}
                </div>
              </section>
            ) : null}

            <section className="space-y-3">
              <h2 className="text-sm font-semibold">Endpoints</h2>
              {overview.endpoints.length === 0 ? (
                <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
                  No public/internal endpoints available.
                </p>
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

            <section className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-sm font-semibold">Recent Deployments</h2>
                <AppLink
                  to={`/services/${encodeURIComponent(decodedServiceId)}/deployments`}
                  className="text-xs font-medium text-primary hover:underline"
                >
                  Open full history
                </AppLink>
              </div>
              <div className="overflow-x-auto rounded-md border border-border">
                <table className="min-w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="px-3 py-2 font-medium text-muted-foreground">Version</th>
                      <th className="px-3 py-2 font-medium text-muted-foreground">Outcome</th>
                      <th className="px-3 py-2 font-medium text-muted-foreground">Deployed At</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.deployments.slice(0, 5).map((deployment) => (
                      <tr key={deployment.id} className="border-b border-border/70">
                        <td className="px-3 py-2">{deployment.version ?? 'N/A'}</td>
                        <td className="px-3 py-2">
                          <span className="inline-flex items-center rounded-full bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
                            State: {deployment.status ?? 'unknown'}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">{formatDate(deployment.deployedAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        ) : null}
      </div>
    </PageShell>
  )
}
