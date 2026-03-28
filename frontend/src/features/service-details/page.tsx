import { useCallback, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { cn } from '@/lib/utils'
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
import type { MonitoringProviderStatus } from '@/lib/api/observability'
import type { DeploymentHistoryItem } from '@/lib/adapters/deployments'
import {
  type ServiceMetricsRange,
  type ServiceMetricsSummary,
  type ServiceMetricsTrends,
} from '@/lib/adapters/service-metrics'
import {
  type LogsQuickViewPreset,
  type LogsQuickViewRange,
} from '@/lib/adapters/logs-quickview'
import type { TimelineWindow } from '@/lib/adapters/service-health-timeline'
import { evaluateDeploymentHistoryItem, summarizeDeploymentAlerts } from '@/lib/deployment-alerts'
import type { ServiceIncidentBadge } from '@/lib/incident-alerts'
import type { ServiceIdentity } from '@/lib/service-identity'
import {
  buildArgoAppUrl,
  buildGrafanaDashboardLink,
  buildGrafanaErrorPanelLink,
  buildGrafanaLatencyPanelLink,
  buildLogsLink,
  config,
} from '@/lib/config'
import { useServiceOverview } from './use-service-overview'
import { useServiceObservability } from './use-service-observability'
import { useServiceActions } from './use-service-actions'
import { DeploymentActionsPanel } from './components/deployment-actions-panel'
import { AdminControlsSection } from './components/admin-controls-section'

// ServiceDetailsPage is the deepest single-service screen in the portal. It fans in
// catalog metadata, release/deployment history, metrics, logs, timeline data, and
// a few admin actions such as deploy/promote/rollback/config edits.
interface ServiceDetailsPageProps {
  serviceId: string
  incidentServiceAlerts?: Record<string, ServiceIncidentBadge>
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

// Distinguish upstream/provider failures from legitimate no-data cases so the UI
// can explain whether the problem is instrumentation coverage or backend reachability.
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

function AdoptServiceSection({ serviceId }: { serviceId: string }) {
  const [projectId, setProjectId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<{ status: string; message: string; prUrl?: string } | null>(null)
  const [error, setError] = useState('')

  const handleAdopt = useCallback(async () => {
    if (!projectId.trim()) return
    setSubmitting(true)
    setError('')
    setResult(null)
    try {
      const { adoptService } = await import('@/lib/api')
      const response = await adoptService(serviceId, projectId.trim())
      setResult({ status: response.status, message: response.message, prUrl: response.prUrl ?? undefined })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to adopt service.')
    } finally {
      setSubmitting(false)
    }
  }, [projectId, serviceId])

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold">Adopt into Project</h2>
      <p className="text-xs text-muted-foreground">
        Link this standalone service to a parent project. This is a metadata-only change (Phase 1 soft-link)
        that creates a PR to add <code>project_id</code> to the service catalog entry.
      </p>
      <div className="flex items-end gap-2">
        <label className="flex-1 space-y-1">
          <span className="text-xs font-medium text-muted-foreground">Project ID</span>
          <input
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            placeholder="e.g. my-project"
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
            disabled={submitting}
          />
        </label>
        <Button onClick={() => void handleAdopt()} disabled={submitting || !projectId.trim()} variant="outline">
          {submitting ? 'Adopting...' : 'Adopt'}
        </Button>
      </div>
      {error ? (
        <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>
      ) : null}
      {result ? (
        <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
          <p className="text-emerald-900 dark:text-emerald-200">{result.message}</p>
          {result.prUrl ? (
            <a href={result.prUrl} target="_blank" rel="noreferrer" className="mt-1 block text-xs text-primary hover:underline">
              View PR
            </a>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}

export function ServiceDetailsPage({ serviceId, incidentServiceAlerts = {} }: ServiceDetailsPageProps) {
  const {
    decodedServiceId,
    serviceIdentity,
    overview,
    projectContext,
    deploymentHistory,
    deploymentInfo,
    deploymentInfoLoading,
    deploymentInfoError,
    isLoading,
    error,
    deploymentHistoryUnavailable,
    deploymentHistoryError,
    loadOverview,
    loadDeploymentInfo,
  } = useServiceOverview(serviceId)
  const {
    metricsRange,
    setMetricsRange,
    metrics,
    metricsLoading,
    metricsError,
    metricTrends,
    metricTrendsLoading,
    metricTrendsError,
    timelineWindow,
    setTimelineWindow,
    timeline,
    timelineLoading,
    timelineError,
    activeLogsPreset,
    setActiveLogsPreset,
    logsRange,
    setLogsRange,
    logsResult,
    logsLoading,
    logsError,
    loadMetrics,
    loadMetricTrends,
    loadTimeline,
    loadQuickViewLogs,
  } = useServiceObservability(serviceIdentity)
  const [logsDrawerOpen, setLogsDrawerOpen] = useState(false)
  const [logsSearch, setLogsSearch] = useState('')
  const [toastMessage, setToastMessage] = useState('')
  const [toastVariant, setToastVariant] = useState<'success' | 'error' | 'info'>('info')
  const {
    rollbackSupported,
    configSupported,
    latestDevDeployment,
    latestProdDeployment,
    recentDevTags,
    recentProdTags,
    devInFlight,
    prodInFlight,
    devLockActive,
    prodLockActive,
    rollbackTargetEnvironment,
    setRollbackTargetEnvironment,
    rollbackCandidates,
    rollbackCandidatesLoading,
    rollbackCandidatesError,
    selectedRollbackTag,
    setSelectedRollbackTag,
    rollbackReason,
    setRollbackReason,
    rollbackSubmitting,
    rollbackError,
    rollbackResult,
    rollbackLockActive,
    rollbackInFlight,
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
    submitRollbackRequest,
    configEnv,
    setConfigEnv,
    configEntries,
    configLoading,
    configError,
    configSelectedValues,
    setConfigSelectedValues,
    configSubmitting,
    configSubmitError,
    configSubmitResult,
    submitConfigEdit,
    loadConfig,
    publicHostEditMode,
    setPublicHostEditMode,
    publicHostValue,
    setPublicHostValue,
    publicHostSubmitting,
    publicHostError,
    publicHostResult,
    submitPublicHostname,
  } = useServiceActions({
    serviceId: decodedServiceId,
    serviceEnv: serviceIdentity.env,
    deploymentHistory,
    deploymentLock: overview?.deploymentLock ?? null,
    initialPublicHost: overview?.publicHost,
    refreshOverview: loadOverview,
    refreshDeploymentInfo: loadDeploymentInfo,
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

  const handleSubmitRollback = useCallback(async () => {
    try {
      const response = await submitRollbackRequest()
      setToastVariant('success')
      setToastMessage(
        response.status === 'noop'
          ? response.message ?? `Rollback not needed for ${rollbackTargetEnvironment}.`
          : `Rollback request accepted for ${decodedServiceId}.`,
      )
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to request service rollback.'
      setToastVariant('error')
      setToastMessage(message)
    }
  }, [decodedServiceId, rollbackTargetEnvironment, submitRollbackRequest])

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
  // Pre-render deep links with the same normalized identity used by quick view so
  // opening Grafana/Loki directly preserves the page's current service scope.
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

            {overview?.observabilityMode === 'no-http' ? (
              <section className="space-y-3">
                <h2 className="text-sm font-semibold">Service Health</h2>
                <p className="text-xs text-muted-foreground">
                  This service declares <code>no-http</code> observability mode. HTTP metrics (latency, error rate,
                  availability) are intentionally not collected.
                </p>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  <article className="rounded-md border border-border bg-background p-4">
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Pod Health</p>
                    <p className="mt-2 text-2xl font-semibold capitalize">{overview.health}</p>
                    <p className="mt-1 text-xs text-muted-foreground">Derived from Argo CD health status</p>
                  </article>
                  <article className="rounded-md border border-border bg-background p-4">
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Sync Status</p>
                    <p className="mt-2 text-2xl font-semibold capitalize">{overview.sync.replace('_', ' ')}</p>
                    <p className="mt-1 text-xs text-muted-foreground">GitOps desired-state alignment</p>
                  </article>
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
            ) : (
            <>
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
            </>
            )}

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

            <DeploymentActionsPanel
              serviceId={decodedServiceId}
              deploymentHistory={deploymentHistory}
              rollbackSupported={rollbackSupported}
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
              rollbackTargetEnvironment={rollbackTargetEnvironment}
              setRollbackTargetEnvironment={setRollbackTargetEnvironment}
              rollbackCandidates={rollbackCandidates}
              rollbackCandidatesLoading={rollbackCandidatesLoading}
              rollbackCandidatesError={rollbackCandidatesError}
              selectedRollbackTag={selectedRollbackTag}
              setSelectedRollbackTag={setSelectedRollbackTag}
              rollbackReason={rollbackReason}
              setRollbackReason={setRollbackReason}
              rollbackSubmitting={rollbackSubmitting}
              rollbackError={rollbackError}
              rollbackResult={rollbackResult}
              rollbackLockActive={Boolean(rollbackLockActive)}
              rollbackInFlight={rollbackInFlight}
              onSubmitRollback={() => void handleSubmitRollback()}
            />

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

            {!projectContext ? (
              <AdoptServiceSection serviceId={decodedServiceId} />
            ) : null}

            <AdminControlsSection
              configSupported={configSupported}
              publicHostEditMode={publicHostEditMode}
              setPublicHostEditMode={setPublicHostEditMode}
              publicHostValue={publicHostValue}
              setPublicHostValue={setPublicHostValue}
              publicHostSubmitting={publicHostSubmitting}
              publicHostError={publicHostError}
              publicHostResult={publicHostResult}
              onSubmitPublicHostname={() => void submitPublicHostname()}
              configEnv={configEnv}
              setConfigEnv={setConfigEnv}
              configEntries={configEntries}
              configLoading={configLoading}
              configError={configError}
              configSelectedValues={configSelectedValues}
              setConfigSelectedValues={setConfigSelectedValues}
              configSubmitting={configSubmitting}
              configSubmitError={configSubmitError}
              configSubmitResult={configSubmitResult}
              onSubmitConfigEdit={(key) => void submitConfigEdit(key)}
              onReloadConfig={(env) => void loadConfig(env)}
            />
          </>
        ) : null}
      </div>
    </PageShell>
  )
}
