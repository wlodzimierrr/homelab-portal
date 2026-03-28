import type { Project, ServiceDetails, ServiceEndpoint } from '@/lib/api/catalog'
import type { MonitoringProviderStatus } from '@/lib/api/observability'
import type {
  ReleaseTraceabilityRow,
  ServiceDeployment,
  ServiceDeploymentLock,
} from '@/lib/api/deployments'
import type { DeploymentHistoryItem } from '@/lib/adapters/deployments'
import type {
  LogsQuickViewPreset,
  LogsQuickViewRange,
} from '@/lib/adapters/logs-quickview'
import type { ServiceMetricsSummary, ServiceMetricsTrends } from '@/lib/adapters/service-metrics'
import type { TimelineWindow } from '@/lib/adapters/service-health-timeline'
import {
  createServiceIdentity,
  normalizeServiceId,
  type ServiceIdentity,
} from '@/lib/service-identity'
import type { MetricSeverity } from '@/components/service-metric-card'

export type HealthStatus = 'healthy' | 'degraded' | 'unknown'
export type SyncStatus = 'synced' | 'out_of_sync' | 'unknown'
export type MonitoringPanelState =
  | 'healthy'
  | 'upstream_unknown'
  | 'provider_unreachable'
  | 'provider_error'
  | 'no_data'
export type ConsoleLogLevel = 'error' | 'warn' | 'info' | 'debug' | 'trace' | 'unknown'

export interface ServiceOverviewData {
  id: string
  name: string
  version: string
  health: HealthStatus
  sync: SyncStatus
  endpoints: ServiceEndpoint[]
  endpointState: 'available' | 'no_routed_endpoint' | 'metadata_missing'
  deployments: ServiceDeployment[]
  deploymentLock?: ServiceDeploymentLock | null
  observabilityMode?: 'app-native' | 'ingress-derived' | 'no-http'
  publicHost?: string
}

export interface LogsPreset {
  id: LogsQuickViewPreset
  label: string
  description: string
  queryTemplate: string
}

export interface QuickLinkCardProps {
  label: string
  description: string
  href?: string
  unavailableMessage?: string
}

export const logsPresets: LogsPreset[] = [
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

export const logsRangeOptions: Array<{ value: LogsQuickViewRange; label: string }> = [
  { value: '15m', label: '15m' },
  { value: '1h', label: '1h' },
  { value: '6h', label: '6h' },
  { value: '24h', label: '24h' },
]

export const timelineWindowOptions: Array<{ value: TimelineWindow; label: string }> = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
]

export function normalizeHealthStatus(value?: string): HealthStatus {
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

export function normalizeSyncStatus(value?: string): SyncStatus {
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

export function formatDate(value?: string) {
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

export function formatConsoleTimestamp(value?: string) {
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

export function safeDecodeServiceId(rawServiceId: string) {
  try {
    return decodeURIComponent(rawServiceId)
  } catch {
    return rawServiceId
  }
}

export function getMetricSeverity(
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

export function buildFromProjects(serviceId: string, projects: Project[]): ServiceOverviewData {
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
    publicHost: primary?.publicUrl,
  }
}

export function buildEndpointList(
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

export function buildIdentityFromServiceDetails(
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

export function deriveVersionFromImageRef(imageRef?: string | null) {
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

export function buildOverviewFromReleaseRows(
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

export function formatGrafanaLinkUnavailable(reason: string | null) {
  return reason ?? 'Unavailable because the Grafana URL template could not be resolved for this service.'
}

export function formatLogsLinkUnavailable(reason: string | null) {
  return reason ?? 'Unavailable because the Grafana/Loki URL template could not be resolved for this service.'
}

export function detectConsoleLogLevel(message: string): ConsoleLogLevel {
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

export function getConsoleLogTone(level: ConsoleLogLevel) {
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

export function formatConsoleLogSource(labels: Record<string, string>, identity: ServiceIdentity) {
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

export function normalizeProviderPanelState(
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

export function buildMetricsCoverageMessage(metrics: ServiceMetricsSummary) {
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

export function buildMetricTrendsCoverageMessage(metrics: ServiceMetricsTrends) {
  if (metrics.observabilityDiagnostics?.status && metrics.observabilityDiagnostics.status !== 'ok') {
    return metrics.observabilityDiagnostics.message ?? ''
  }
  return ''
}

export function formatTrendSource(source?: 'app_metrics' | 'traefik_fallback') {
  if (source === 'app_metrics') {
    return 'App metrics'
  }
  if (source === 'traefik_fallback') {
    return 'Ingress fallback'
  }
  return 'No source'
}

export function formatDeploymentAction(action?: string | null) {
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

export function getDeploymentOutcomeTone(outcome?: string | null) {
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

export function getLatestDeploymentForEnv(deployments: DeploymentHistoryItem[], env: 'dev' | 'prod') {
  return deployments.find((deployment) => deployment.identity.env === env)
}

export function getRecentDeploymentTags(deployments: DeploymentHistoryItem[], env: 'dev' | 'prod', limit = 3) {
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
