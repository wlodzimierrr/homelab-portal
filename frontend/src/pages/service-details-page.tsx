import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { ServiceMetricTrendChart } from '@/components/service-metric-trend-chart'
import { ServiceHealthTimeline } from '@/components/service-health-timeline'
import { ServiceMetricCard, type MetricSeverity } from '@/components/service-metric-card'
import { StatusCard } from '@/components/status-card'
import { ToastMessage } from '@/components/toast-message'
import { UptimeIndicator } from '@/components/uptime-indicator'
import { Button } from '@/components/ui/button'
import {
  getProjects,
  getReleaseTraceability,
  getServiceDeploymentInfo,
  getServiceRollbackCandidates,
  getService,
  type MonitoringProviderStatus,
  type Project,
  type ReleaseTraceabilityRow,
  requestServiceDeployToDev,
  requestServicePromoteToProd,
  requestServiceRollback,
  type ServiceDeploymentInfo,
  type ServiceDetails,
  type ServiceDeployment,
  type ServiceDeploymentLock,
  type ServiceDeployToDevResponse,
  type ServiceEndpoint,
  type ServicePromoteToProdResponse,
  type ServiceRollbackCandidatesResponse,
  type ServiceRollbackResponse,
} from '@/lib/api'
import { getDeploymentHistory, type DeploymentHistoryItem } from '@/lib/adapters/deployments'
import {
  createEmptyServiceMetricsSummary,
  createEmptyServiceMetricsTrends,
  getServiceMetricsSummary,
  getServiceMetricsTrends,
  type ServiceMetricsRange,
  type ServiceMetricsSummary,
  type ServiceMetricsTrends,
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
import { createServiceIdentity, normalizeServiceId, type ServiceIdentity } from '@/lib/service-identity'
import {
  buildArgoAppUrl,
  buildGrafanaDashboardLink,
  buildGrafanaErrorPanelLink,
  buildGrafanaLatencyPanelLink,
  buildLogsLink,
  config,
} from '@/lib/config'

interface ServiceDetailsPageProps {
  serviceId: string
  incidentServiceAlerts?: Record<string, ServiceIncidentBadge>
}

type HealthStatus = 'healthy' | 'degraded' | 'unknown'
type SyncStatus = 'synced' | 'out_of_sync' | 'unknown'

function supportsServiceRollback(serviceId: string) {
  return serviceId === 'homelab-api' || serviceId === 'homelab-web'
}

interface ServiceOverviewData {
  id: string
  name: string
  version: string
  health: HealthStatus
  sync: SyncStatus
  endpoints: ServiceEndpoint[]
  endpointState: 'available' | 'no_routed_endpoint' | 'metadata_missing'
  deployments: ServiceDeployment[]
  deploymentLock?: ServiceDeploymentLock | null
}

interface QuickLinkCardProps {
  label: string
  description: string
  href?: string
  unavailableMessage?: string
}

interface LogsPreset {
  id: LogsQuickViewPreset
  label: string
  description: string
  queryTemplate: string
}

type MonitoringPanelState =
  | 'healthy'
  | 'upstream_unknown'
  | 'provider_unreachable'
  | 'provider_error'
  | 'no_data'

type ConsoleLogLevel = 'error' | 'warn' | 'info' | 'debug' | 'trace' | 'unknown'

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

function formatConsoleTimestamp(value?: string) {
  if (!value) {
    return '--:--:--'
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return '--:--:--'
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(parsed)
}

function safeDecodeServiceId(rawServiceId: string) {
  try {
    return decodeURIComponent(rawServiceId)
  } catch {
    return rawServiceId
  }
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
  const canonicalServiceId = normalizeServiceId(serviceId)
  const matches = projects.filter((project) => {
    const projectId = normalizeServiceId(project.id)
    const projectName = normalizeServiceId(project.name)
    return projectId === canonicalServiceId || projectName === canonicalServiceId
  })
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
    endpointState:
      endpointMap.size > 0 ? 'available' : matches.length > 0 ? 'no_routed_endpoint' : 'metadata_missing',
    deployments: [],
    deploymentLock: null,
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

function buildIdentityFromServiceDetails(
  serviceId: string,
  details: ServiceDetails,
  fallback: ServiceIdentity,
): ServiceIdentity {
  return createServiceIdentity({
    serviceId: details.id || fallback.serviceId || serviceId,
    serviceName: details.name || fallback.serviceName,
    namespace: details.namespace || details.identity?.namespace || fallback.namespace,
    env: details.env || details.identity?.env || fallback.env,
    appLabel: details.appLabel || details.identity?.appLabel || fallback.appLabel,
    argoAppName: details.argoAppName || details.identity?.argoAppName || fallback.argoAppName,
  })
}

function deriveVersionFromImageRef(imageRef?: string | null) {
  if (!imageRef) {
    return undefined
  }
  const trimmed = imageRef.trim()
  if (!trimmed) {
    return undefined
  }
  const parts = trimmed.split(':')
  return parts.length > 1 ? parts.slice(1).join(':') : trimmed
}

function buildOverviewFromReleaseRows(
  serviceId: string,
  rows: ReleaseTraceabilityRow[],
): Partial<ServiceOverviewData> {
  const canonicalId = normalizeServiceId(serviceId)
  const matchingRows = rows
    .filter((row) => normalizeServiceId(typeof row.serviceId === 'string' ? row.serviceId : '') === canonicalId)
    .sort((left, right) => {
      const leftTime = left.deployedAt ? new Date(left.deployedAt).getTime() : 0
      const rightTime = right.deployedAt ? new Date(right.deployedAt).getTime() : 0
      return rightTime - leftTime
    })

  if (matchingRows.length === 0) {
    return {}
  }

  const latest = matchingRows[0]

  return {
    version: deriveVersionFromImageRef(latest.imageRef) ?? 'N/A',
    health: normalizeHealthStatus(
      typeof latest.argo?.healthStatus === 'string' ? latest.argo.healthStatus : undefined,
    ),
    sync: normalizeSyncStatus(
      typeof latest.argo?.syncStatus === 'string' ? latest.argo.syncStatus : undefined,
    ),
    deployments: matchingRows.map((row, index) => ({
      id:
        (typeof row.commitSha === 'string' && row.commitSha) ||
        (typeof row.deployedAt === 'string' && row.deployedAt) ||
        `${serviceId}:${index}`,
      version: deriveVersionFromImageRef(row.imageRef),
      status:
        typeof row.argo?.healthStatus === 'string'
          ? row.argo.healthStatus
          : typeof row.argo?.syncStatus === 'string'
            ? row.argo.syncStatus
            : undefined,
      deployedAt: typeof row.deployedAt === 'string' ? row.deployedAt : undefined,
    })),
  }
}

function QuickLinkCard({ label, description, href, unavailableMessage }: QuickLinkCardProps) {
  if (!href || href.trim() === '') {
    return (
      <div className="rounded-md border border-border bg-background p-3">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
        <p className="mt-2 text-xs text-muted-foreground">
          {unavailableMessage ?? 'Unavailable due to missing URL configuration.'}
        </p>
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

function formatGrafanaLinkUnavailable(reason: string | null) {
  return reason ?? 'Unavailable because the Grafana URL template could not be resolved for this service.'
}

function formatLogsLinkUnavailable(reason: string | null) {
  return reason ?? 'Unavailable because the Grafana/Loki URL template could not be resolved for this service.'
}

function detectConsoleLogLevel(message: string): ConsoleLogLevel {
  const normalized = message.toLowerCase()
  if (normalized.includes('error') || normalized.includes('exception') || normalized.includes('fatal')) {
    return 'error'
  }
  if (normalized.includes('warn')) {
    return 'warn'
  }
  if (normalized.includes('info')) {
    return 'info'
  }
  if (normalized.includes('debug')) {
    return 'debug'
  }
  if (normalized.includes('trace')) {
    return 'trace'
  }
  return 'unknown'
}

function getConsoleLogTone(level: ConsoleLogLevel) {
  switch (level) {
    case 'error':
      return {
        row: 'border-rose-950/70 bg-rose-950/45',
        text: 'text-rose-300',
        badge: 'bg-rose-500/20 text-rose-200',
      }
    case 'warn':
      return {
        row: 'border-amber-950/70 bg-amber-950/35',
        text: 'text-amber-300',
        badge: 'bg-amber-500/20 text-amber-200',
      }
    case 'info':
      return {
        row: 'border-sky-950/60 bg-sky-950/25',
        text: 'text-sky-300',
        badge: 'bg-sky-500/20 text-sky-200',
      }
    case 'debug':
      return {
        row: 'border-emerald-950/60 bg-emerald-950/20',
        text: 'text-emerald-300',
        badge: 'bg-emerald-500/20 text-emerald-200',
      }
    case 'trace':
      return {
        row: 'border-violet-950/60 bg-violet-950/20',
        text: 'text-violet-300',
        badge: 'bg-violet-500/20 text-violet-200',
      }
    default:
      return {
        row: 'border-zinc-900 bg-zinc-950/50',
        text: 'text-zinc-100',
        badge: 'bg-zinc-800 text-zinc-300',
      }
  }
}

function formatConsoleLogSource(labels: Record<string, string>, identity: ServiceIdentity) {
  const pod = labels.pod || labels.pod_name
  const container = labels.container || labels.container_name
  const app = labels.app || labels.app_kubernetes_io_name || identity.appLabel
  if (pod && container) {
    return `[${pod}/${container}]`
  }
  if (pod) {
    return `[${pod}]`
  }
  if (container) {
    return `[${container}]`
  }
  if (app) {
    return `[${app}]`
  }
  return '[service]'
}

function normalizeProviderPanelState(
  providerStatus?: MonitoringProviderStatus,
  errorMessage?: string,
  noData = false,
): MonitoringPanelState {
  const normalizedProviderState = providerStatus?.status?.toLowerCase()
  const normalizedError = errorMessage?.toLowerCase() ?? ''

  if (normalizedProviderState === 'unreachable' || normalizedError.includes('unreachable')) {
    return 'provider_unreachable'
  }
  if (
    normalizedProviderState === 'auth_error' ||
    normalizedProviderState === 'http_error' ||
    normalizedProviderState === 'bad_payload' ||
    normalizedError.includes('auth_error') ||
    normalizedError.includes('http_error') ||
    normalizedError.includes('bad_payload')
  ) {
    return 'provider_error'
  }
  if (noData) {
    return 'no_data'
  }
  if (!providerStatus) {
    return 'upstream_unknown'
  }
  return 'healthy'
}

function MonitoringPanelNotice({
  provider,
  state,
}: {
  provider: string
  state: MonitoringPanelState
}) {
  if (state === 'healthy') {
    return null
  }

  const config =
    state === 'provider_unreachable'
      ? {
          title: 'Provider unreachable',
          body: `${provider} could not be reached. This is a backend/provider path issue, not a no-data result.`,
          tone: 'border-rose-500/40 bg-rose-500/10 text-rose-900 dark:text-rose-200',
        }
      : state === 'provider_error'
        ? {
            title: 'Provider error',
            body: `${provider} responded, but the backend could not use the response cleanly.`,
            tone: 'border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200',
          }
        : state === 'no_data'
          ? {
              title: 'No data',
              body: `${provider} is reachable, but no matching data was returned for the current scope or time range.`,
              tone: 'border-slate-500/40 bg-slate-500/10 text-slate-900 dark:text-slate-200',
            }
          : {
              title: 'Upstream unknown',
              body: `${provider} readiness metadata is unavailable, so this panel cannot classify the upstream state precisely.`,
              tone: 'border-border bg-muted/30 text-muted-foreground',
            }

  return (
    <div className={`rounded-md border p-3 ${config.tone}`}>
      <p className="text-sm font-medium">{config.title}</p>
      <p className="mt-1 text-xs">{config.body}</p>
    </div>
  )
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

function buildMetricTrendsCoverageMessage(metrics: ServiceMetricsTrends) {
  if (metrics.observabilityDiagnostics?.status && metrics.observabilityDiagnostics.status !== 'ok') {
    return metrics.observabilityDiagnostics.message ?? ''
  }
  return ''
}

function formatTrendSource(source?: 'app_metrics' | 'traefik_fallback') {
  if (source === 'app_metrics') {
    return 'App metrics'
  }
  if (source === 'traefik_fallback') {
    return 'Ingress fallback'
  }
  return 'No source'
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

function formatDeploymentAction(action?: string | null) {
  switch ((action ?? '').trim().toLowerCase()) {
    case 'deploy':
      return 'Deploy'
    case 'promote':
      return 'Promote'
    case 'rollback':
      return 'Rollback'
    case 'config-change':
      return 'Config change'
    default:
      return action || 'Unknown'
  }
}

function getDeploymentOutcomeTone(outcome?: string | null) {
  const normalized = (outcome ?? '').trim().toLowerCase()
  if (normalized === 'live') {
    return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  }
  if (normalized === 'deploying' || normalized === 'pending') {
    return 'bg-sky-500/10 text-sky-700 dark:text-sky-300'
  }
  if (normalized === 'failed') {
    return 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
  }
  return 'bg-muted text-muted-foreground'
}

function getLatestDeploymentForEnv(deployments: DeploymentHistoryItem[], env: 'dev' | 'prod') {
  return deployments.find((deployment) => deployment.identity.env === env)
}

function getRecentDeploymentTags(deployments: DeploymentHistoryItem[], env: 'dev' | 'prod', limit = 3) {
  const seen = new Set<string>()
  const items: string[] = []
  for (const deployment of deployments) {
    if (deployment.identity.env !== env || !deployment.version || deployment.version === 'N/A') {
      continue
    }
    if (seen.has(deployment.version)) {
      continue
    }
    seen.add(deployment.version)
    items.push(deployment.version)
    if (items.length >= limit) {
      break
    }
  }
  return items
}

const logsPresets: LogsPreset[] = [
  {
    id: 'all',
    label: 'All logs',
    description: 'Recent logs without severity filtering',
    queryTemplate: '{namespace="{{namespace}}", app="{{app_label}}"}',
  },
  {
    id: 'errors',
    label: 'Errors',
    description: 'HTTP 5xx or error-level logs',
    queryTemplate: '{namespace="{{namespace}}", app="{{app_label}}"} |~ "(?i)(error| 5[0-9][0-9])"',
  },
  {
    id: 'restarts',
    label: 'Restarts',
    description: 'Container restart signals and crash loops',
    queryTemplate: '{namespace="{{namespace}}", app="{{app_label}}"} |~ "(?i)(restart|CrashLoopBackOff)"',
  },
  {
    id: 'warnings',
    label: 'Warnings',
    description: 'Recent warning/timeout style signals',
    queryTemplate: '{namespace="{{namespace}}", app="{{app_label}}"} |~ "(?i)(warn|timeout)"',
  },
]

const logsRangeOptions: Array<{ value: LogsQuickViewRange; label: string }> = [
  { value: '15m', label: '15m' },
  { value: '1h', label: '1h' },
  { value: '6h', label: '6h' },
  { value: '24h', label: '24h' },
]

const timelineWindowOptions: Array<{ value: TimelineWindow; label: string }> = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
]

export function ServiceDetailsPage({ serviceId, incidentServiceAlerts = {} }: ServiceDetailsPageProps) {
  const decodedServiceId = useMemo(() => safeDecodeServiceId(serviceId), [serviceId])
  const [serviceIdentity, setServiceIdentity] = useState<ServiceIdentity>(() =>
    createServiceIdentity({ serviceId: decodedServiceId }),
  )
  const [overview, setOverview] = useState<ServiceOverviewData | null>(null)
  const [deploymentHistory, setDeploymentHistory] = useState<DeploymentHistoryItem[]>([])
  const [deploymentInfo, setDeploymentInfo] = useState<ServiceDeploymentInfo | null>(null)
  const [deploymentInfoLoading, setDeploymentInfoLoading] = useState(true)
  const [deploymentInfoError, setDeploymentInfoError] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [metricsRange, setMetricsRange] = useState<ServiceMetricsRange>('24h')
  const [metrics, setMetrics] = useState<ServiceMetricsSummary>(() =>
    createEmptyServiceMetricsSummary(decodedServiceId, '24h'),
  )
  const [metricsLoading, setMetricsLoading] = useState(true)
  const [metricsError, setMetricsError] = useState('')
  const [metricTrends, setMetricTrends] = useState<ServiceMetricsTrends>(() =>
    createEmptyServiceMetricsTrends(decodedServiceId, '24h'),
  )
  const [metricTrendsLoading, setMetricTrendsLoading] = useState(true)
  const [metricTrendsError, setMetricTrendsError] = useState('')
  const [timelineWindow, setTimelineWindow] = useState<TimelineWindow>('24h')
  const [timeline, setTimeline] = useState<ServiceHealthTimelineData | null>(null)
  const [timelineLoading, setTimelineLoading] = useState(true)
  const [timelineError, setTimelineError] = useState('')
  const [logsDrawerOpen, setLogsDrawerOpen] = useState(false)
  const [activeLogsPreset, setActiveLogsPreset] = useState<LogsQuickViewPreset>('all')
  const [logsRange, setLogsRange] = useState<LogsQuickViewRange>('1h')
  const [logsSearch, setLogsSearch] = useState('')
  const [logsResult, setLogsResult] = useState<ServiceLogsQuickView | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)
  const [logsError, setLogsError] = useState('')
  const [deploymentHistoryUnavailable, setDeploymentHistoryUnavailable] = useState(false)
  const [deploymentHistoryError, setDeploymentHistoryError] = useState('')
  const [rollbackTargetEnvironment, setRollbackTargetEnvironment] = useState<'dev' | 'prod'>('dev')
  const [rollbackCandidates, setRollbackCandidates] = useState<ServiceRollbackCandidatesResponse | null>(null)
  const [rollbackCandidatesLoading, setRollbackCandidatesLoading] = useState(false)
  const [rollbackCandidatesError, setRollbackCandidatesError] = useState('')
  const [selectedRollbackTag, setSelectedRollbackTag] = useState('')
  const [rollbackReason, setRollbackReason] = useState('')
  const [rollbackSubmitting, setRollbackSubmitting] = useState(false)
  const [rollbackError, setRollbackError] = useState('')
  const [rollbackResult, setRollbackResult] = useState<ServiceRollbackResponse | null>(null)
  const [deployReason, setDeployReason] = useState('')
  const [deploySubmitting, setDeploySubmitting] = useState(false)
  const [deployError, setDeployError] = useState('')
  const [deployResult, setDeployResult] = useState<ServiceDeployToDevResponse | null>(null)
  const [promoteReason, setPromoteReason] = useState('')
  const [promoteSubmitting, setPromoteSubmitting] = useState(false)
  const [promoteError, setPromoteError] = useState('')
  const [promoteResult, setPromoteResult] = useState<ServicePromoteToProdResponse | null>(null)
  const [toastMessage, setToastMessage] = useState('')
  const [toastVariant, setToastVariant] = useState<'success' | 'error' | 'info'>('info')

  const loadOverview = useCallback(async () => {
    setIsLoading(true)
    setError('')
    setDeploymentHistoryUnavailable(false)
    setDeploymentHistoryError('')

    try {
      const identity = await getServiceIdentity(decodedServiceId).catch(() =>
        createServiceIdentity({ serviceId: decodedServiceId }),
      )
      setServiceIdentity(identity)

      const [serviceResult, projectsResult, deploymentsResult, releasesResult] = await Promise.allSettled([
        getService(decodedServiceId),
        getProjects(),
        getDeploymentHistory(identity, { limit: 20 }),
        getReleaseTraceability({ serviceId: decodedServiceId, limit: 20 }),
      ])

      const fallback =
        projectsResult.status === 'fulfilled'
          ? buildFromProjects(decodedServiceId, projectsResult.value.projects)
          : buildFromProjects(decodedServiceId, [])

      if (serviceResult.status === 'fulfilled') {
        setServiceIdentity(buildIdentityFromServiceDetails(decodedServiceId, serviceResult.value, identity))
      }

      const releaseFallback = releasesResult.status === 'fulfilled'
        ? buildOverviewFromReleaseRows(decodedServiceId, releasesResult.value)
        : {}

      const finalOverview: ServiceOverviewData =
        serviceResult.status === 'fulfilled'
          ? {
              id: serviceResult.value.id || decodedServiceId,
              name: serviceResult.value.name || decodedServiceId,
              version: serviceResult.value.version ?? releaseFallback.version ?? 'N/A',
              health:
                normalizeHealthStatus(serviceResult.value.health) === 'unknown'
                  ? (releaseFallback.health ?? 'unknown')
                  : normalizeHealthStatus(serviceResult.value.health),
              sync:
                normalizeSyncStatus(serviceResult.value.sync) === 'unknown'
                  ? (releaseFallback.sync ?? 'unknown')
                  : normalizeSyncStatus(serviceResult.value.sync),
              endpoints: buildEndpointList(
                serviceResult.value.endpoints,
                serviceResult.value.publicUrl,
                serviceResult.value.internalUrls,
              ),
              endpointState: 'no_routed_endpoint',
              deployments: releaseFallback.deployments ?? [],
              deploymentLock: serviceResult.value.deploymentLock ?? null,
            }
          : fallback

      if (serviceResult.status !== 'fulfilled') {
        finalOverview.version = releaseFallback.version ?? finalOverview.version
        finalOverview.health = releaseFallback.health ?? finalOverview.health
        finalOverview.sync = releaseFallback.sync ?? finalOverview.sync
        if ((finalOverview.deployments?.length ?? 0) === 0 && releaseFallback.deployments?.length) {
          finalOverview.deployments = releaseFallback.deployments
        }
      }

      if (finalOverview.endpoints.length === 0 && fallback.endpoints.length > 0) {
        finalOverview.endpoints = fallback.endpoints
        finalOverview.endpointState = 'available'
      } else if (finalOverview.endpoints.length > 0) {
        finalOverview.endpointState = 'available'
      } else if (serviceResult.status !== 'fulfilled') {
        finalOverview.endpointState = fallback.endpointState
      }

      if (deploymentsResult.status === 'fulfilled') {
        setDeploymentHistory(deploymentsResult.value)
        finalOverview.deployments = deploymentsResult.value.map((deployment) => ({
          id: deployment.id,
          version: deployment.version,
          status: deployment.outcome,
          deployedAt: deployment.deployedAt,
        }))
        if (finalOverview.deployments.length === 0 && releaseFallback.deployments?.length) {
          finalOverview.deployments = releaseFallback.deployments
        }
      } else if (releaseFallback.deployments?.length) {
        setDeploymentHistory([])
        finalOverview.deployments = releaseFallback.deployments
      } else {
        setDeploymentHistory([])
        setDeploymentHistoryUnavailable(true)
        setDeploymentHistoryError(
          deploymentsResult.reason instanceof Error
            ? deploymentsResult.reason.message
            : 'Deployment history is unavailable right now.',
        )
      }

      setOverview(finalOverview)
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load service overview'
      setDeploymentHistory([])
      setError(message)
    } finally {
      setIsLoading(false)
    }
  }, [decodedServiceId])

  useEffect(() => {
    void loadOverview()
  }, [loadOverview])

  const loadDeploymentInfo = useCallback(async () => {
    setDeploymentInfoLoading(true)
    setDeploymentInfoError('')

    try {
      const response = await getServiceDeploymentInfo(decodedServiceId, serviceIdentity.env || undefined)
      setDeploymentInfo(response)
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load deployment info'
      setDeploymentInfoError(message)
      setDeploymentInfo(null)
    } finally {
      setDeploymentInfoLoading(false)
    }
  }, [decodedServiceId, serviceIdentity.env])

  useEffect(() => {
    void loadDeploymentInfo()
  }, [loadDeploymentInfo])

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

  const loadMetricTrends = useCallback(async () => {
    setMetricTrendsLoading(true)
    setMetricTrendsError('')

    try {
      const response = await getServiceMetricsTrends(serviceIdentity, metricsRange)
      setMetricTrends(response)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load service metric trends'
      setMetricTrendsError(message)
      setMetricTrends(createEmptyServiceMetricsTrends(serviceIdentity, metricsRange))
    } finally {
      setMetricTrendsLoading(false)
    }
  }, [metricsRange, serviceIdentity])

  useEffect(() => {
    void loadMetricTrends()
  }, [loadMetricTrends])

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

  const argoUrl = useMemo(
    () => buildArgoAppUrl(serviceIdentity.serviceId || decodedServiceId, serviceIdentity.argoAppName),
    [decodedServiceId, serviceIdentity.argoAppName, serviceIdentity.serviceId],
  )
  const grafanaTimeRange = metricsRange
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
  const presetLinks = useMemo(() => {
    return logsPresets.map((preset) => ({
      ...preset,
      query: preset.queryTemplate
        .replaceAll('{{namespace}}', serviceIdentity.namespace)
        .replaceAll('{{app_label}}', serviceIdentity.appLabel),
      link: buildLogsLink({
        serviceId: serviceIdentity.serviceId,
        namespace: serviceIdentity.namespace,
        environment: serviceIdentity.env,
        appLabel: serviceIdentity.appLabel,
        argoAppName: serviceIdentity.argoAppName,
        timeRange: logsRange,
        preset: preset.id,
        query: preset.queryTemplate
          .replaceAll('{{namespace}}', serviceIdentity.namespace)
          .replaceAll('{{app_label}}', serviceIdentity.appLabel),
      }),
    }))
  }, [
    logsRange,
    serviceIdentity.appLabel,
    serviceIdentity.argoAppName,
    serviceIdentity.env,
    serviceIdentity.namespace,
    serviceIdentity.serviceId,
  ])
  const activePreset = useMemo(
    () => presetLinks.find((preset) => preset.id === activeLogsPreset) ?? presetLinks[0],
    [activeLogsPreset, presetLinks],
  )
  const logsUrl = activePreset?.link.href ?? ''
  const consoleTimezone = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local time'
    } catch {
      return 'Local time'
    }
  }, [])
  const filteredConsoleLines = useMemo(() => {
    const lines = logsResult?.lines ?? []
    const query = logsSearch.trim().toLowerCase()
    if (!query) {
      return lines
    }
    return lines.filter((line) => {
      const labelText = Object.values(line.labels).join(' ').toLowerCase()
      return line.message.toLowerCase().includes(query) || labelText.includes(query)
    })
  }, [logsResult?.lines, logsSearch])
  const metricsAllNoData = useMemo(
    () =>
      metrics.noData.uptimePct &&
      metrics.noData.p95LatencyMs &&
      metrics.noData.errorRatePct &&
      metrics.noData.restartCount,
    [metrics],
  )
  const metricsPanelState = useMemo(
    () =>
      normalizeProviderPanelState(
        metrics.providerStatus,
        metricsError,
        metricsAllNoData && !metrics.observabilityDiagnostics?.message,
      ),
    [metrics.providerStatus, metricsAllNoData, metrics.observabilityDiagnostics?.message, metricsError],
  )
  const metricsCoverageMessage = useMemo(
    () => buildMetricsCoverageMessage(metrics),
    [metrics],
  )
  const metricTrendsAllNoData = useMemo(
    () => metricTrends.p95LatencyMs.queryStatus === 'no_data' && metricTrends.errorRatePct.queryStatus === 'no_data',
    [metricTrends],
  )
  const metricTrendsPanelState = useMemo(
    () =>
      normalizeProviderPanelState(
        metricTrends.providerStatus,
        metricTrendsError,
        metricTrendsAllNoData && !metricTrends.observabilityDiagnostics?.message,
      ),
    [
      metricTrends.providerStatus,
      metricTrendsAllNoData,
      metricTrends.observabilityDiagnostics?.message,
      metricTrendsError,
    ],
  )
  const metricTrendsCoverageMessage = useMemo(
    () => buildMetricTrendsCoverageMessage(metricTrends),
    [metricTrends],
  )
  const logsPanelState = useMemo(
    () =>
      normalizeProviderPanelState(
        logsResult?.providerStatus,
        logsError,
        !logsLoading && !logsError && (logsResult?.lines.length ?? 0) === 0,
      ),
    [logsError, logsLoading, logsResult],
  )
  const rollbackSupported = useMemo(() => supportsServiceRollback(decodedServiceId), [decodedServiceId])
  const latestDevDeployment = useMemo(
    () => getLatestDeploymentForEnv(deploymentHistory, 'dev'),
    [deploymentHistory],
  )
  const latestProdDeployment = useMemo(
    () => getLatestDeploymentForEnv(deploymentHistory, 'prod'),
    [deploymentHistory],
  )
  const recentDevTags = useMemo(() => getRecentDeploymentTags(deploymentHistory, 'dev'), [deploymentHistory])
  const recentProdTags = useMemo(() => getRecentDeploymentTags(deploymentHistory, 'prod'), [deploymentHistory])
  const devInFlight = useMemo(
    () =>
      deploymentHistory.some(
        (deployment) =>
          deployment.identity.env === 'dev' &&
          (deployment.outcome === 'pending' || deployment.outcome === 'deploying'),
      ),
    [deploymentHistory],
  )
  const prodInFlight = useMemo(
    () =>
      deploymentHistory.some(
        (deployment) =>
          deployment.identity.env === 'prod' &&
          (deployment.outcome === 'pending' || deployment.outcome === 'deploying'),
      ),
    [deploymentHistory],
  )
  const devLockActive = overview?.deploymentLock?.env === 'dev'
  const prodLockActive = overview?.deploymentLock?.env === 'prod'

  useEffect(() => {
    if (serviceIdentity.env === 'dev' || serviceIdentity.env === 'prod') {
      setRollbackTargetEnvironment(serviceIdentity.env)
    }
  }, [serviceIdentity.env])

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

  const loadRollbackCandidates = useCallback(async () => {
    if (!rollbackSupported) {
      setRollbackCandidates(null)
      setRollbackCandidatesError('')
      setSelectedRollbackTag('')
      return
    }

    setRollbackCandidatesLoading(true)
    setRollbackCandidatesError('')

    try {
      const response = await getServiceRollbackCandidates(decodedServiceId, rollbackTargetEnvironment)
      setRollbackCandidates(response)
      setSelectedRollbackTag((current) => {
        if (current && response.candidates.some((candidate) => candidate.tag === current)) {
          return current
        }
        return response.candidates[0]?.tag ?? ''
      })
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load rollback candidates.'
      setRollbackCandidatesError(message)
      setRollbackCandidates(null)
      setSelectedRollbackTag('')
    } finally {
      setRollbackCandidatesLoading(false)
    }
  }, [decodedServiceId, rollbackSupported, rollbackTargetEnvironment])

  const submitDeployRequest = useCallback(async () => {
    setDeploySubmitting(true)
    setDeployError('')
    setDeployResult(null)

    try {
      const response = await requestServiceDeployToDev(decodedServiceId, {
        deployReason,
      })
      setDeployResult(response)
      setDeployReason('')
      setToastVariant('success')
      setToastMessage(
        response.status === 'noop'
          ? response.message ?? `Dev already points at ${response.newTag ?? 'the latest image'}.`
          : `Deploy request accepted for ${decodedServiceId}.`,
      )
      void loadOverview()
      void loadDeploymentInfo()
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to request deploy to dev.'
      setDeployError(message)
      setToastVariant('error')
      setToastMessage(message)
    } finally {
      setDeploySubmitting(false)
    }
  }, [decodedServiceId, deployReason, loadDeploymentInfo, loadOverview])

  const submitPromoteRequest = useCallback(async () => {
    setPromoteSubmitting(true)
    setPromoteError('')
    setPromoteResult(null)

    try {
      const response = await requestServicePromoteToProd(decodedServiceId, {
        deployReason: promoteReason,
      })
      setPromoteResult(response)
      setPromoteReason('')
      setToastVariant('success')
      setToastMessage(
        response.status === 'noop'
          ? response.message ?? `Prod already matches ${response.newTag ?? 'the dev tag'}.`
          : `Promote request accepted for ${decodedServiceId}.`,
      )
      void loadOverview()
      void loadDeploymentInfo()
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to request promote to prod.'
      setPromoteError(message)
      setToastVariant('error')
      setToastMessage(message)
    } finally {
      setPromoteSubmitting(false)
    }
  }, [decodedServiceId, loadDeploymentInfo, loadOverview, promoteReason])

  const submitRollbackRequest = useCallback(async () => {
    setRollbackSubmitting(true)
    setRollbackError('')
    setRollbackResult(null)

    try {
      const response = await requestServiceRollback(decodedServiceId, {
        targetEnvironment: rollbackTargetEnvironment,
        rollbackTag: selectedRollbackTag,
        deployReason: rollbackReason,
      })
      setRollbackResult(response)
      setRollbackReason('')
      setToastVariant('success')
      setToastMessage(
        response.status === 'noop'
          ? response.message ?? `Rollback not needed for ${rollbackTargetEnvironment}.`
          : `Rollback request accepted for ${decodedServiceId}.`,
      )
      void loadOverview()
      void loadDeploymentInfo()
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to request service rollback.'
      setRollbackError(message)
      setToastVariant('error')
      setToastMessage(message)
    } finally {
      setRollbackSubmitting(false)
    }
  }, [
    decodedServiceId,
    loadDeploymentInfo,
    loadOverview,
    rollbackReason,
    rollbackTargetEnvironment,
    selectedRollbackTag,
  ])

  useEffect(() => {
    void loadQuickViewLogs()
  }, [loadQuickViewLogs])

  useEffect(() => {
    void loadRollbackCandidates()
  }, [loadRollbackCandidates])

  useEffect(() => {
    const interval = window.setInterval(() => {
      void loadOverview()
      void loadDeploymentInfo()
    }, 30000)
    return () => window.clearInterval(interval)
  }, [loadDeploymentInfo, loadOverview])

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
                <p className="mt-1 text-xs text-muted-foreground">
                  {overview.version && overview.version !== 'N/A'
                    ? 'Resolved from live release metadata.'
                    : 'Deployment metadata is not available for this service yet.'}
                </p>
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

            {rollbackSupported ? (
              <section className="space-y-3 rounded-md border border-border bg-card p-4">
                <div className="space-y-1">
                  <h2 className="text-sm font-semibold">Portal Actions</h2>
                  <p className="text-xs text-muted-foreground">
                    Request deploy and promote actions directly from the service page. Buttons are blocked while a
                    deployment lock or in-flight deployment exists for the target environment.
                  </p>
                </div>
                <div className="grid gap-4 xl:grid-cols-2">
                  <article className="space-y-3 rounded-md border border-border/70 bg-background/50 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">Deploy latest to dev</h3>
                      <p className="text-xs text-muted-foreground">
                        Opens a GitOps PR that updates the dev overlay to the newest deployable image.
                      </p>
                    </div>
                    {devLockActive || devInFlight ? (
                      <span className="rounded-full bg-sky-500/10 px-2 py-1 text-xs font-medium text-sky-700 dark:text-sky-300">
                        Dev locked
                      </span>
                    ) : null}
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-md border border-border/70 bg-background p-3 text-xs text-muted-foreground">
                      <p className="font-medium text-foreground">Current dev tag</p>
                      <p className="mt-1 break-all">{latestDevDeployment?.version ?? 'Unavailable'}</p>
                    </div>
                    <div className="rounded-md border border-border/70 bg-background p-3 text-xs text-muted-foreground">
                      <p className="font-medium text-foreground">Recent dev tags</p>
                      <div className="mt-1 flex flex-wrap gap-2">
                        {recentDevTags.map((tag) => (
                          <code key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
                            {tag}
                          </code>
                        ))}
                        {recentDevTags.length === 0 ? <span>Unavailable</span> : null}
                      </div>
                    </div>
                  </div>
                  <label className="space-y-1">
                    <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Deploy reason</span>
                    <textarea
                      value={deployReason}
                      onChange={(event) => setDeployReason(event.target.value)}
                      rows={3}
                      placeholder="Why the latest build should be deployed to dev."
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    />
                  </label>
                  {deployError ? (
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3">
                      <p className="text-xs text-destructive">{deployError}</p>
                    </div>
                  ) : null}
                  {deployResult ? (
                    <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-950 dark:text-emerald-200">
                      <p className="font-medium">
                        {deployResult.status === 'noop'
                          ? deployResult.message ?? 'Dev already points at the latest deployable image.'
                          : `Deploy request accepted for dev.`}
                      </p>
                      {deployResult.gitPrUrl ? (
                        <a href={deployResult.gitPrUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex font-medium underline underline-offset-2">
                          Open GitOps PR #{deployResult.gitPrNumber ?? 'link'}
                        </a>
                      ) : null}
                    </div>
                  ) : null}
                  <Button
                    type="button"
                    onClick={() => void submitDeployRequest()}
                    disabled={deploySubmitting || devLockActive || devInFlight || deployReason.trim().length < 5}
                  >
                    {deploySubmitting ? 'Requesting deploy...' : 'Deploy latest to dev'}
                  </Button>
                  </article>

                  <article className="space-y-3 rounded-md border border-border/70 bg-background/50 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">Promote dev to prod</h3>
                      <p className="text-xs text-muted-foreground">
                        Opens a GitOps PR that copies the current dev tag into the prod overlay for this service.
                      </p>
                    </div>
                    {prodLockActive || prodInFlight ? (
                      <span className="rounded-full bg-sky-500/10 px-2 py-1 text-xs font-medium text-sky-700 dark:text-sky-300">
                        Prod locked
                      </span>
                    ) : null}
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-md border border-border/70 bg-background p-3 text-xs text-muted-foreground">
                      <p className="font-medium text-foreground">Current dev tag</p>
                      <p className="mt-1 break-all">{latestDevDeployment?.version ?? 'Unavailable'}</p>
                    </div>
                    <div className="rounded-md border border-border/70 bg-background p-3 text-xs text-muted-foreground">
                      <p className="font-medium text-foreground">Current prod tag</p>
                      <p className="mt-1 break-all">{latestProdDeployment?.version ?? 'Unavailable'}</p>
                    </div>
                  </div>
                  <div className="rounded-md border border-border/70 bg-background p-3 text-xs text-muted-foreground">
                    <p className="font-medium text-foreground">Recent prod tags</p>
                    <div className="mt-1 flex flex-wrap gap-2">
                      {recentProdTags.map((tag) => (
                        <code key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
                          {tag}
                        </code>
                      ))}
                      {recentProdTags.length === 0 ? <span>Unavailable</span> : null}
                    </div>
                  </div>
                  <label className="space-y-1">
                    <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Promote reason</span>
                    <textarea
                      value={promoteReason}
                      onChange={(event) => setPromoteReason(event.target.value)}
                      rows={3}
                      placeholder="Why the current dev tag should be promoted to prod."
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    />
                  </label>
                  {promoteError ? (
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3">
                      <p className="text-xs text-destructive">{promoteError}</p>
                    </div>
                  ) : null}
                  {promoteResult ? (
                    <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-950 dark:text-emerald-200">
                      <p className="font-medium">
                        {promoteResult.status === 'noop'
                          ? promoteResult.message ?? 'Prod already matches dev.'
                          : `Promote request accepted for prod.`}
                      </p>
                      {promoteResult.gitPrUrl ? (
                        <a href={promoteResult.gitPrUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex font-medium underline underline-offset-2">
                          Open GitOps PR #{promoteResult.gitPrNumber ?? 'link'}
                        </a>
                      ) : null}
                    </div>
                  ) : null}
                  <Button
                    type="button"
                    onClick={() => void submitPromoteRequest()}
                    disabled={promoteSubmitting || prodLockActive || prodInFlight || promoteReason.trim().length < 5}
                  >
                    {promoteSubmitting ? 'Requesting promote...' : 'Promote to prod'}
                  </Button>
                  </article>
                </div>
              </section>
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
              {!metricsLoading ? <MonitoringPanelNotice provider="Prometheus" state={metricsPanelState} /> : null}
              {!metricsLoading && !metricsError && metricsCoverageMessage ? (
                <div className="rounded-md border border-slate-500/40 bg-slate-500/10 p-3">
                  <p className="text-xs text-slate-900 dark:text-slate-200">{metricsCoverageMessage}</p>
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
                Portal-native trend cards backed by the same Prometheus fallback logic as the API summary, with
                Grafana kept as the drill-down path.
              </p>
              {metricTrendsError ? (
                <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
                  <p className="text-xs text-amber-900 dark:text-amber-200">{metricTrendsError}</p>
                  <Button type="button" size="sm" variant="outline" className="mt-2" onClick={() => void loadMetricTrends()}>
                    Retry trends
                  </Button>
                </div>
              ) : null}
              {!metricTrendsLoading ? (
                <MonitoringPanelNotice provider="Prometheus" state={metricTrendsPanelState} />
              ) : null}
              {!metricTrendsLoading && !metricTrendsError && metricTrendsCoverageMessage ? (
                <div className="rounded-md border border-slate-500/40 bg-slate-500/10 p-3">
                  <p className="text-xs text-slate-900 dark:text-slate-200">{metricTrendsCoverageMessage}</p>
                </div>
              ) : null}
              <div className="grid gap-3 xl:grid-cols-2">
                <article className="space-y-4 rounded-md border border-border bg-background p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">P95 Latency Trend</h3>
                      <p className="text-xs text-muted-foreground">
                        Current service scope over the last {grafanaTimeRange}.
                      </p>
                    </div>
                    {latencyPanelLink.href ? (
                      <Button asChild size="sm" variant="outline">
                        <a href={latencyPanelLink.href} target="_blank" rel="noreferrer">
                          Open in Grafana
                        </a>
                      </Button>
                    ) : (
                      <Button type="button" size="sm" variant="outline" disabled>
                        Open in Grafana
                      </Button>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span>Source: {formatTrendSource(metricTrends.p95LatencyMs.querySource)}</span>
                    <span>
                      Latest:{' '}
                      {typeof metricTrends.p95LatencyMs.latestValue === 'number'
                        ? `${Math.round(metricTrends.p95LatencyMs.latestValue)} ms`
                        : 'N/A'}
                    </span>
                  </div>
                  {metricTrendsLoading ? (
                    <LoadingState label="Loading latency trend..." rows={3} />
                  ) : (
                    <ServiceMetricTrendChart
                      points={metricTrends.p95LatencyMs.points}
                      color="#38bdf8"
                      fill="rgba(56, 189, 248, 0.18)"
                      formatValue={(value) => `${Math.round(value)} ms`}
                    />
                  )}
                  {metricTrends.p95LatencyMs.queryStatus === 'no_data' && !metricTrendsLoading ? (
                    <p className="text-xs text-muted-foreground">
                      {metricTrends.p95LatencyMs.queryMessage ?? 'No retained latency samples were available.'}
                    </p>
                  ) : null}
                  {!latencyPanelLink.href ? (
                    <p className="text-xs text-muted-foreground">
                      {formatGrafanaLinkUnavailable(latencyPanelLink.reason)}
                    </p>
                  ) : null}
                </article>
                <article className="space-y-4 rounded-md border border-border bg-background p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">Error Rate Trend</h3>
                      <p className="text-xs text-muted-foreground">
                        Current service scope over the last {grafanaTimeRange}.
                      </p>
                    </div>
                    {errorPanelLink.href ? (
                      <Button asChild size="sm" variant="outline">
                        <a href={errorPanelLink.href} target="_blank" rel="noreferrer">
                          Open in Grafana
                        </a>
                      </Button>
                    ) : (
                      <Button type="button" size="sm" variant="outline" disabled>
                        Open in Grafana
                      </Button>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span>Source: {formatTrendSource(metricTrends.errorRatePct.querySource)}</span>
                    <span>
                      Latest:{' '}
                      {typeof metricTrends.errorRatePct.latestValue === 'number'
                        ? `${metricTrends.errorRatePct.latestValue.toFixed(2)}%`
                        : 'N/A'}
                    </span>
                  </div>
                  {metricTrendsLoading ? (
                    <LoadingState label="Loading error-rate trend..." rows={3} />
                  ) : (
                    <ServiceMetricTrendChart
                      points={metricTrends.errorRatePct.points}
                      color="#f97316"
                      fill="rgba(249, 115, 22, 0.18)"
                      formatValue={(value) => `${value.toFixed(2)}%`}
                    />
                  )}
                  {metricTrends.errorRatePct.queryStatus === 'no_data' && !metricTrendsLoading ? (
                    <p className="text-xs text-muted-foreground">
                      {metricTrends.errorRatePct.queryMessage ?? 'No retained error-rate samples were available.'}
                    </p>
                  ) : null}
                  {!errorPanelLink.href ? (
                    <p className="text-xs text-muted-foreground">
                      {formatGrafanaLinkUnavailable(errorPanelLink.reason)}
                    </p>
                  ) : null}
                </article>
              </div>
            </section>

            <section className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold">Logs Console</h2>
                  <p className="text-xs text-muted-foreground">
                    Live Loki quick-view lines scoped to this service. Full logs still open in Grafana or Loki.
                  </p>
                </div>
                <div className="flex items-center gap-2">
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
                  {activePreset?.link.href ? (
                    <Button asChild size="sm" variant="outline">
                      <a href={activePreset.link.href} target="_blank" rel="noreferrer">
                        Open full logs
                      </a>
                    </Button>
                  ) : (
                    <Button type="button" size="sm" variant="outline" disabled>
                      Open full logs
                    </Button>
                  )}
                </div>
              </div>
              {!logsLoading ? <MonitoringPanelNotice provider="Loki" state={logsPanelState} /> : null}
              <div className="rounded-md border border-zinc-800 bg-zinc-950 text-zinc-100 shadow-sm">
                <div className="grid gap-2 border-b border-zinc-800 px-4 py-3 xl:grid-cols-[160px_minmax(0,1fr)_160px_auto_auto]">
                  <select
                    value={activeLogsPreset}
                    onChange={(event) => setActiveLogsPreset(event.target.value as LogsQuickViewPreset)}
                    className="rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
                  >
                    {presetLinks.map((preset) => (
                      <option key={preset.id} value={preset.id}>
                        {preset.label}
                      </option>
                    ))}
                  </select>
                  <input
                    value={logsSearch}
                    onChange={(event) => setLogsSearch(event.target.value)}
                    placeholder="Search current console lines"
                    className="rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500"
                  />
                  <select
                    value={logsRange}
                    onChange={(event) => setLogsRange(event.target.value as LogsQuickViewRange)}
                    className="rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
                  >
                    {logsRangeOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        Last {option.label}
                      </option>
                    ))}
                  </select>
                  <div className="flex items-center justify-center rounded-md border border-zinc-700 px-3 py-2 text-xs text-zinc-300">
                    {consoleTimezone}
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="border-zinc-700 text-zinc-100 hover:bg-zinc-800"
                      onClick={() => void loadQuickViewLogs()}
                    >
                      Refresh
                    </Button>
                    {activePreset?.link.href ? (
                      <Button asChild size="sm" variant="outline" className="border-zinc-700 text-zinc-100 hover:bg-zinc-800">
                        <a href={activePreset.link.href} target="_blank" rel="noreferrer">
                          Open full logs
                        </a>
                      </Button>
                    ) : (
                      <Button type="button" size="sm" variant="outline" className="border-zinc-700 text-zinc-100" disabled>
                        Open full logs
                      </Button>
                    )}
                  </div>
                </div>
                <div className="border-b border-zinc-800 px-4 py-2 font-mono text-[11px] text-zinc-400">
                  $ scope={serviceIdentity.namespace}/{serviceIdentity.appLabel} env={serviceIdentity.env} preset=
                  {activePreset?.id ?? activeLogsPreset} range={logsRange} provider=loki
                </div>
                {!activePreset?.link.href ? (
                  <div className="border-b border-zinc-800 px-4 py-3 text-xs text-zinc-400">
                    {formatLogsLinkUnavailable(activePreset?.link.reason ?? null)}
                  </div>
                ) : null}
                {logsLoading ? (
                  <div className="px-4 py-6">
                    <LoadingState label="Loading logs..." rows={3} />
                  </div>
                ) : null}
                {!logsLoading && logsError ? (
                  <div className="px-4 py-4">
                    <div className="rounded-md border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200">
                      <p>{logsError}</p>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="mt-3 border-zinc-700 text-zinc-100 hover:bg-zinc-800"
                        onClick={() => void loadQuickViewLogs()}
                      >
                        Retry logs
                      </Button>
                    </div>
                  </div>
                ) : null}
                {!logsLoading && !logsError ? (
                  <div className="max-h-[28rem] overflow-y-auto font-mono text-xs">
                    {filteredConsoleLines.length === 0 ? (
                      <div className="px-4 py-6 text-zinc-400">
                        <p>[no_data] No logs found for this preset, range, and search.</p>
                      </div>
                    ) : (
                      <div className="divide-y divide-zinc-900/80">
                        {filteredConsoleLines.map((line) => {
                          const level = detectConsoleLogLevel(line.message)
                          const tone = getConsoleLogTone(level)
                          const source = formatConsoleLogSource(line.labels, serviceIdentity)
                          const segments = line.message.split('\n').filter((segment) => segment.trim().length > 0)
                          const [head, ...rest] = segments.length > 0 ? segments : [line.message]

                          return (
                            <article
                              key={`${line.timestamp}-${line.message.slice(0, 40)}`}
                              className={`border-l-2 px-4 py-2 ${tone.row} ${tone.text}`}
                            >
                              <div className="grid gap-x-3 gap-y-1 md:grid-cols-[92px_150px_74px_minmax(0,1fr)]">
                                <span className="text-zinc-400">{formatConsoleTimestamp(line.timestamp)}</span>
                                <span className="text-zinc-500">{source}</span>
                                <span className={`inline-flex w-fit items-center rounded px-2 py-0.5 text-[10px] uppercase tracking-wide ${tone.badge}`}>
                                  {level}
                                </span>
                                <span className="break-words whitespace-pre-wrap">{head}</span>
                                {rest.map((segment, index) => (
                                  <div key={index} className="contents">
                                    <span />
                                    <span />
                                    <span />
                                    <span className="break-words whitespace-pre-wrap text-zinc-300">{segment}</span>
                                  </div>
                                ))}
                              </div>
                            </article>
                          )
                        })}
                      </div>
                    )}
                  </div>
                ) : null}
                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-800 px-4 py-3 text-[11px] text-zinc-400">
                  <p>
                    Showing {filteredConsoleLines.length} of {logsResult?.returned ?? 0} line(s)
                    {logsResult?.moreAvailable ? '; more logs are available.' : '.'}
                  </p>
                  <p>Last refreshed: {formatDate(logsResult?.generatedAt)}</p>
                </div>
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
                    {timelineWindowOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
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
                  unavailableMessage="Unavailable because the Argo CD base URL or application path template is not configured."
                />
                <QuickLinkCard
                  label="Grafana Dashboard"
                  description="Open service metrics dashboard"
                  href={grafanaDashboardLink.href}
                  unavailableMessage={formatGrafanaLinkUnavailable(grafanaDashboardLink.reason)}
                />
                <div className="rounded-md border border-border bg-background p-3">
                  <p className="text-sm font-medium">Logs</p>
                  <p className="mb-3 text-xs text-muted-foreground">
                    Opens Grafana/Loki filtered by namespace, app label, and time range.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" variant="outline" onClick={() => setLogsDrawerOpen((open) => !open)}>
                      {logsDrawerOpen ? 'Hide logs panel' : 'View logs'}
                    </Button>
                    {logsUrl ? (
                      <Button asChild size="sm">
                        <a href={logsUrl} target="_blank" rel="noreferrer">
                          Open full logs
                        </a>
                      </Button>
                    ) : (
                      <Button type="button" size="sm" disabled>
                        Full logs unavailable
                      </Button>
                    )}
                  </div>
                  {!logsUrl ? (
                    <p className="mt-2 text-xs text-muted-foreground">
                      {formatLogsLinkUnavailable(activePreset?.link.reason ?? null)}
                    </p>
                  ) : null}
                </div>
              </div>
            </section>

            {logsDrawerOpen ? (
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
                {!logsLoading ? <MonitoringPanelNotice provider="Loki" state={logsPanelState} /> : null}
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
                    {activePreset?.link.href ? (
                      <Button asChild size="sm">
                        <a href={activePreset.link.href} target="_blank" rel="noreferrer">
                          Open full logs
                        </a>
                      </Button>
                    ) : (
                      <Button type="button" size="sm" disabled>
                        Open full logs
                      </Button>
                    )}
                  </div>
                  {!activePreset?.link.href ? (
                    <p className="mb-3 text-xs text-muted-foreground">
                      {formatLogsLinkUnavailable(activePreset?.link.reason ?? null)}
                    </p>
                  ) : null}
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

            {rollbackSupported ? (
              <section className="space-y-3 rounded-md border border-border bg-card p-4">
                <div className="space-y-1">
                  <h2 className="text-sm font-semibold">Portal Rollback</h2>
                  <p className="text-xs text-muted-foreground">
                    Request a Git-backed rollback for this service. The portal lists previous deployable tags from
                    GitHub Packages, opens a rollback PR, and records the request as a first-class deployment event.
                  </p>
                </div>
                <div className="grid gap-3 md:grid-cols-[180px_minmax(0,1fr)]">
                  <label className="space-y-1">
                    <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Target environment
                    </span>
                    <select
                      value={rollbackTargetEnvironment}
                      onChange={(event) => setRollbackTargetEnvironment(event.target.value as 'dev' | 'prod')}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    >
                      <option value="dev">dev</option>
                      <option value="prod">prod</option>
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Rollback target tag
                    </span>
                    <select
                      value={selectedRollbackTag}
                      onChange={(event) => setSelectedRollbackTag(event.target.value)}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                      disabled={rollbackCandidatesLoading || (rollbackCandidates?.candidates.length ?? 0) === 0}
                    >
                      {(rollbackCandidates?.candidates ?? []).map((candidate) => (
                        <option key={candidate.tag} value={candidate.tag}>
                          {candidate.tag}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-md border border-border/70 bg-background/50 p-3 text-xs text-muted-foreground">
                    <p className="font-medium text-foreground">Current tag</p>
                    <p className="mt-1 break-all">{rollbackCandidates?.currentTag ?? 'Unavailable'}</p>
                  </div>
                  <div className="rounded-md border border-border/70 bg-background/50 p-3 text-xs text-muted-foreground">
                    <p className="font-medium text-foreground">Recent deployed tags</p>
                    <div className="mt-1 flex flex-wrap gap-2">
                      {getRecentDeploymentTags(deploymentHistory, rollbackTargetEnvironment).map((tag) => (
                        <code key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
                          {tag}
                        </code>
                      ))}
                      {getRecentDeploymentTags(deploymentHistory, rollbackTargetEnvironment).length === 0 ? (
                        <span>Unavailable</span>
                      ) : null}
                    </div>
                  </div>
                </div>
                {rollbackCandidatesError ? (
                  <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3">
                    <p className="text-xs text-destructive">{rollbackCandidatesError}</p>
                  </div>
                ) : null}
                {!rollbackCandidatesError && !rollbackCandidatesLoading && rollbackCandidates ? (
                  <div className="rounded-md border border-border/70 bg-background/50 p-3 text-xs text-muted-foreground">
                    <p className="font-medium text-foreground">Available rollback candidates</p>
                    {rollbackCandidates.candidates.length > 0 ? (
                      <div className="mt-2 space-y-2">
                        {rollbackCandidates.candidates.slice(0, 5).map((candidate) => (
                          <div key={candidate.tag} className="flex flex-wrap items-center gap-2">
                            <code className="rounded bg-muted px-1.5 py-0.5 text-[11px]">{candidate.tag}</code>
                            {candidate.publishedAt ? <span>published {candidate.publishedAt}</span> : null}
                            {candidate.compareUrl ? (
                              <a
                                href={candidate.compareUrl}
                                target="_blank"
                                rel="noreferrer"
                                className="font-medium text-primary hover:underline"
                              >
                                Compare
                              </a>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="mt-2">No previous deployable tags are available right now.</p>
                    )}
                  </div>
                ) : null}
                <label className="space-y-1">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Reason</span>
                  <textarea
                    value={rollbackReason}
                    onChange={(event) => setRollbackReason(event.target.value)}
                    rows={3}
                    placeholder="Why this rollback is needed and what known-good version you are restoring."
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  />
                </label>
                {rollbackError ? (
                  <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3">
                    <p className="text-xs text-destructive">{rollbackError}</p>
                  </div>
                ) : null}
                {rollbackResult ? (
                  <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-950 dark:text-emerald-200">
                    <p className="font-medium">
                      {rollbackResult.status === 'noop'
                        ? `Rollback not needed for ${rollbackResult.targetEnvironment}.`
                        : `Rollback request accepted for ${rollbackResult.targetEnvironment}.`}
                    </p>
                    {rollbackResult.gitPrUrl ? (
                      <a
                        href={rollbackResult.gitPrUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-2 inline-flex font-medium underline underline-offset-2"
                      >
                        Open GitOps PR #{rollbackResult.gitPrNumber ?? 'link'}
                      </a>
                    ) : null}
                  </div>
                ) : null}
                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    type="button"
                    onClick={() => void submitRollbackRequest()}
                    disabled={
                      rollbackSubmitting ||
                      rollbackCandidatesLoading ||
                      selectedRollbackTag.trim().length === 0 ||
                      rollbackReason.trim().length < 5
                    }
                  >
                    {rollbackSubmitting ? 'Requesting rollback...' : `Request ${rollbackTargetEnvironment} rollback`}
                  </Button>
                  <p className="text-xs text-muted-foreground">
                    Existing rollback records remain visible in the deployment history view for operator audit.
                  </p>
                </div>
              </section>
            ) : null}

            <section className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">Deploy Info</h2>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void loadDeploymentInfo()}
                  disabled={deploymentInfoLoading}
                >
                  Refresh deploy info
                </Button>
              </div>
              {deploymentInfoError ? (
                <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
                  <p className="text-xs text-amber-900 dark:text-amber-200">{deploymentInfoError}</p>
                </div>
              ) : null}
              {deploymentInfoLoading ? (
                <LoadingState label="Loading deploy info..." rows={2} />
              ) : deploymentInfo ? (
                <div className="grid gap-3 xl:grid-cols-2">
                  <article className="rounded-md border border-border bg-background p-4">
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Current deployment</p>
                    <div className="mt-2 space-y-2 text-sm">
                      <p>
                        <span className="font-medium">Action:</span> {formatDeploymentAction(deploymentInfo.action)}
                      </p>
                      <p>
                        <span className="font-medium">Result:</span>{' '}
                        <span
                          className={`inline-flex rounded-full px-2 py-1 text-xs font-medium ${getDeploymentOutcomeTone(
                            deploymentInfo.result,
                          )}`}
                        >
                          {deploymentInfo.result ?? 'unknown'}
                        </span>
                      </p>
                      <p>
                        <span className="font-medium">Deployed:</span>{' '}
                        {formatDate(deploymentInfo.deployedTimestamp ?? undefined)}
                      </p>
                      <p className="break-all">
                        <span className="font-medium">Image:</span>{' '}
                        {deploymentInfo.imageUrl ? (
                          <a href={deploymentInfo.imageUrl} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                            {deploymentInfo.deployedImage ?? 'N/A'}
                          </a>
                        ) : (
                          deploymentInfo.deployedImage ?? 'N/A'
                        )}
                      </p>
                      {deploymentInfo.previousImage ? (
                        <p className="break-all">
                          <span className="font-medium">Previous image:</span> {deploymentInfo.previousImage}
                        </p>
                      ) : null}
                      {deploymentInfo.imageDigest ? (
                        <p className="break-all">
                          <span className="font-medium">Digest:</span> {deploymentInfo.imageDigest}
                        </p>
                      ) : null}
                    </div>
                  </article>
                  <article className="rounded-md border border-border bg-background p-4">
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Traceability</p>
                    <div className="mt-2 space-y-2 text-sm">
                      <p>
                        <span className="font-medium">Commit:</span>{' '}
                        {deploymentInfo.commitUrl ? (
                          <a href={deploymentInfo.commitUrl} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                            {deploymentInfo.gitCommit ?? 'N/A'}
                          </a>
                        ) : (
                          deploymentInfo.gitCommit ?? 'N/A'
                        )}
                      </p>
                      <p>
                        <span className="font-medium">Argo:</span> {deploymentInfo.argoApp ?? 'N/A'}
                      </p>
                      <p>
                        <span className="font-medium">Sync/health:</span> {deploymentInfo.syncStatus ?? 'unknown'} /{' '}
                        {deploymentInfo.healthStatus ?? 'unknown'}
                      </p>
                      {deploymentInfo.gitPrUrl ? (
                        <p>
                          <span className="font-medium">PR:</span>{' '}
                          <a href={deploymentInfo.gitPrUrl} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                            #{deploymentInfo.gitPrNumber ?? 'link'}
                          </a>
                        </p>
                      ) : null}
                      {deploymentInfo.compareUrl ? (
                        <p>
                          <span className="font-medium">Compare:</span>{' '}
                          <a href={deploymentInfo.compareUrl} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                            Open compare view
                          </a>
                        </p>
                      ) : null}
                      {deploymentInfo.deployReason ? (
                        <p>
                          <span className="font-medium">Reason:</span> {deploymentInfo.deployReason}
                        </p>
                      ) : null}
                      {deploymentInfo.resultReason ? (
                        <p>
                          <span className="font-medium">Result reason:</span> {deploymentInfo.resultReason}
                        </p>
                      ) : null}
                    </div>
                  </article>
                </div>
              ) : (
                <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
                  No deployment info is available for this service yet.
                </p>
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
              {deploymentHistoryUnavailable ? (
                <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
                  <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
                    Deployment history unavailable
                  </p>
                  <p className="mt-1 text-xs text-amber-900 dark:text-amber-200">
                    {deploymentHistoryError || 'Live deployment history could not be loaded.'}
                  </p>
                  <Button type="button" size="sm" variant="outline" className="mt-3" onClick={() => void loadOverview()}>
                    Retry deployment history
                  </Button>
                </div>
              ) : deploymentHistory.length === 0 ? (
                <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
                  No deployments found for this service yet.
                </p>
              ) : (
                <div className="overflow-x-auto rounded-md border border-border">
                  <table className="min-w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-border">
                        <th className="px-3 py-2 font-medium text-muted-foreground">Env</th>
                        <th className="px-3 py-2 font-medium text-muted-foreground">Action</th>
                        <th className="px-3 py-2 font-medium text-muted-foreground">Version</th>
                        <th className="px-3 py-2 font-medium text-muted-foreground">Status</th>
                        <th className="px-3 py-2 font-medium text-muted-foreground">Reason / links</th>
                        <th className="px-3 py-2 font-medium text-muted-foreground">Deployed At</th>
                      </tr>
                    </thead>
                    <tbody>
                      {deploymentHistory.slice(0, 5).map((deployment) => (
                        <tr key={deployment.id} className="border-b border-border/70">
                          <td className="px-3 py-2 align-top">{deployment.identity.env}</td>
                          <td className="px-3 py-2 align-top">{formatDeploymentAction(deployment.action)}</td>
                          <td className="px-3 py-2 align-top">{deployment.version ?? 'N/A'}</td>
                          <td className="px-3 py-2">
                            <span
                              className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-medium ${getDeploymentOutcomeTone(
                                deployment.outcome,
                              )}`}
                            >
                              {deployment.outcome ?? 'unknown'}
                            </span>
                          </td>
                          <td className="px-3 py-2 align-top text-xs text-muted-foreground">
                            {deployment.deployReason ? <p>{deployment.deployReason}</p> : <p>N/A</p>}
                            <div className="mt-1 flex flex-wrap gap-2">
                              {deployment.gitPrUrl ? (
                                <a
                                  href={deployment.gitPrUrl}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="font-medium text-primary hover:underline"
                                >
                                  PR #{deployment.gitPrNumber ?? 'link'}
                                </a>
                              ) : null}
                              {deployment.compareUrl ? (
                                <a
                                  href={deployment.compareUrl}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="font-medium text-primary hover:underline"
                                >
                                  Compare
                                </a>
                              ) : null}
                            </div>
                            {deployment.failureReason ? <p className="mt-1 text-rose-400">{deployment.failureReason}</p> : null}
                          </td>
                          <td className="px-3 py-2 align-top text-muted-foreground">
                            {formatDate(deployment.deployedAt ?? deployment.requestedAt)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        ) : null}
      </div>
    </PageShell>
  )
}
