import { request, type MonitoringProviderStatus } from '@/lib/api'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'
import type { LogsQuickViewPreset, LogsQuickViewLine } from '@/lib/adapters/logs-quickview'
import type { ServiceHealthTimelineSegment } from '@/lib/adapters/service-health-timeline'

export interface DeploymentObservabilityMetricSnapshot {
  before?: number
  after?: number
  delta?: number
}

export interface DeploymentObservabilityContext {
  serviceId: string
  env?: string
  deploymentId?: string
  action?: string
  status?: string
  windowStart?: string
  windowEnd?: string
  windowSource: 'deployment_record' | 'explicit_window'
  evidenceStatus: 'resolved' | 'missing'
  evidenceMessage?: string
  compareUrl?: string
  gitPrUrl?: string
  gitPrNumber?: number
  deployReason?: string
}

export interface DeploymentObservabilityMetrics {
  queryStatus: 'ok' | 'no_data' | 'no_deployment_window'
  queryMessage?: string
  windowStart?: string
  windowEnd?: string
  generatedAt?: string
  errorRatePct?: DeploymentObservabilityMetricSnapshot
  p95LatencyMs?: DeploymentObservabilityMetricSnapshot
  availabilityPct?: DeploymentObservabilityMetricSnapshot
  noData: {
    errorRatePct: boolean
    p95LatencyMs: boolean
    availabilityPct: boolean
  }
  providerStatus?: MonitoringProviderStatus
}

export interface DeploymentObservabilityTimeline {
  queryStatus: 'ok' | 'no_data' | 'no_deployment_window'
  queryMessage?: string
  serviceId: string
  windowStart?: string
  windowEnd?: string
  generatedAt?: string
  providerStatus?: MonitoringProviderStatus
  segments: ServiceHealthTimelineSegment[]
}

export interface DeploymentObservabilityLogs {
  queryStatus: 'ok' | 'no_data' | 'no_deployment_window'
  queryMessage?: string
  serviceId: string
  preset: LogsQuickViewPreset
  generatedAt?: string
  windowStart?: string
  windowEnd?: string
  limit: number
  returned: number
  moreAvailable: boolean
  lines: LogsQuickViewLine[]
  providerStatus?: MonitoringProviderStatus
}

export interface DeploymentObservability {
  serviceId: string
  context: DeploymentObservabilityContext
  metrics: DeploymentObservabilityMetrics
  healthTimeline: DeploymentObservabilityTimeline
  logsQuickView: DeploymentObservabilityLogs
}

interface DeploymentObservabilityOptions {
  deploymentId?: string
  windowStart?: string
  windowEnd?: string
  logsPreset?: LogsQuickViewPreset
  logsLimit?: number
}

interface ApiTimelineSegment {
  start?: string
  end?: string
  status?: string
  reason?: string
}

interface ApiDeploymentObservabilityResponse {
  serviceId?: string
  context?: Record<string, unknown>
  metrics?: Record<string, unknown>
  healthTimeline?: {
    queryStatus?: DeploymentObservabilityTimeline['queryStatus']
    queryMessage?: string
    serviceId?: string
    windowStart?: string
    windowEnd?: string
    generatedAt?: string
    providerStatus?: MonitoringProviderStatus
    segments?: ApiTimelineSegment[]
  }
  logsQuickView?: {
    queryStatus?: DeploymentObservabilityLogs['queryStatus']
    queryMessage?: string
    serviceId?: string
    preset?: string
    generatedAt?: string
    windowStart?: string
    windowEnd?: string
    limit?: number
    returned?: number
    moreAvailable?: boolean
    lines?: Array<{
      timestamp?: string
      message?: string
      labels?: Record<string, string>
    }>
    providerStatus?: MonitoringProviderStatus
  }
}

function resolveIdentity(input: ServiceIdentity | string) {
  if (typeof input === 'string') {
    return createServiceIdentity({ serviceId: input })
  }
  return createServiceIdentity(input)
}

function asNumber(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function adaptMetricSnapshot(value: unknown): DeploymentObservabilityMetricSnapshot | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined
  }
  const record = value as Record<string, unknown>
  return {
    before: asNumber(record.before),
    after: asNumber(record.after),
    delta: asNumber(record.delta),
  }
}

function adaptTimelineSegments(value: ApiTimelineSegment[] | undefined): ServiceHealthTimelineSegment[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .map((segment, index): ServiceHealthTimelineSegment | null => {
      const startAt = typeof segment.start === 'string' ? segment.start : ''
      const endAt = typeof segment.end === 'string' ? segment.end : ''
      if (!startAt || !endAt) {
        return null
      }
      const adapted: ServiceHealthTimelineSegment = {
        id: `segment-${index}-${startAt}`,
        startAt,
        endAt,
        status:
          segment.status === 'healthy' || segment.status === 'degraded' || segment.status === 'down'
            ? segment.status
            : 'unknown',
      }
      if (typeof segment.reason === 'string') {
        adapted.reason = segment.reason
      }
      return adapted
    })
    .filter((segment): segment is ServiceHealthTimelineSegment => segment !== null)
}

function adaptLogsLines(
  value:
    | Array<{
        timestamp?: string
        message?: string
        labels?: Record<string, string>
      }>
    | undefined,
): LogsQuickViewLine[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .map((line) => {
      if (typeof line.timestamp !== 'string' || typeof line.message !== 'string') {
        return null
      }
      return {
        timestamp: line.timestamp,
        message: line.message,
        labels: line.labels && typeof line.labels === 'object' && !Array.isArray(line.labels) ? line.labels : {},
      }
    })
    .filter((line): line is LogsQuickViewLine => line !== null)
}

export async function getDeploymentObservability(
  service: ServiceIdentity | string,
  options: DeploymentObservabilityOptions,
): Promise<DeploymentObservability> {
  const identity = resolveIdentity(service)
  const params = new URLSearchParams()
  if (options.deploymentId) {
    params.set('deploymentId', options.deploymentId)
  }
  if (options.windowStart) {
    params.set('windowStart', options.windowStart)
  }
  if (options.windowEnd) {
    params.set('windowEnd', options.windowEnd)
  }
  if (options.logsPreset) {
    params.set('logsPreset', options.logsPreset)
  }
  if (typeof options.logsLimit === 'number' && Number.isFinite(options.logsLimit)) {
    params.set('logsLimit', String(Math.max(1, Math.floor(options.logsLimit))))
  }

  const payload = await request<ApiDeploymentObservabilityResponse>(
    `/services/${encodeURIComponent(identity.serviceId)}/observability/window?${params.toString()}`,
  )

  const context = payload.context ?? {}
  const metrics = payload.metrics ?? {}
  const timeline = payload.healthTimeline ?? {}
  const logs = payload.logsQuickView ?? {}

  return {
    serviceId: typeof payload.serviceId === 'string' ? payload.serviceId : identity.serviceId,
    context: {
      serviceId: typeof context.serviceId === 'string' ? context.serviceId : identity.serviceId,
      env: typeof context.env === 'string' ? context.env : undefined,
      deploymentId: typeof context.deploymentId === 'string' ? context.deploymentId : undefined,
      action: typeof context.action === 'string' ? context.action : undefined,
      status: typeof context.status === 'string' ? context.status : undefined,
      windowStart: typeof context.windowStart === 'string' ? context.windowStart : undefined,
      windowEnd: typeof context.windowEnd === 'string' ? context.windowEnd : undefined,
      windowSource:
        context.windowSource === 'explicit_window' ? 'explicit_window' : 'deployment_record',
      evidenceStatus: context.evidenceStatus === 'missing' ? 'missing' : 'resolved',
      evidenceMessage: typeof context.evidenceMessage === 'string' ? context.evidenceMessage : undefined,
      compareUrl: typeof context.compareUrl === 'string' ? context.compareUrl : undefined,
      gitPrUrl: typeof context.gitPrUrl === 'string' ? context.gitPrUrl : undefined,
      gitPrNumber: asNumber(context.gitPrNumber),
      deployReason: typeof context.deployReason === 'string' ? context.deployReason : undefined,
    },
    metrics: {
      queryStatus: metrics.queryStatus === 'no_deployment_window' || metrics.queryStatus === 'no_data' ? metrics.queryStatus : 'ok',
      queryMessage: typeof metrics.queryMessage === 'string' ? metrics.queryMessage : undefined,
      windowStart: typeof metrics.windowStart === 'string' ? metrics.windowStart : undefined,
      windowEnd: typeof metrics.windowEnd === 'string' ? metrics.windowEnd : undefined,
      generatedAt: typeof metrics.generatedAt === 'string' ? metrics.generatedAt : undefined,
      errorRatePct: adaptMetricSnapshot(metrics.errorRatePct),
      p95LatencyMs: adaptMetricSnapshot(metrics.p95LatencyMs),
      availabilityPct: adaptMetricSnapshot(metrics.availabilityPct),
      noData:
        metrics.noData && typeof metrics.noData === 'object' && !Array.isArray(metrics.noData)
          ? {
              errorRatePct: Boolean((metrics.noData as Record<string, unknown>).errorRatePct),
              p95LatencyMs: Boolean((metrics.noData as Record<string, unknown>).p95LatencyMs),
              availabilityPct: Boolean((metrics.noData as Record<string, unknown>).availabilityPct),
            }
          : {
              errorRatePct: true,
              p95LatencyMs: true,
              availabilityPct: true,
            },
      providerStatus: metrics.providerStatus as MonitoringProviderStatus | undefined,
    },
    healthTimeline: {
      queryStatus:
        timeline.queryStatus === 'no_deployment_window' || timeline.queryStatus === 'no_data'
          ? timeline.queryStatus
          : 'ok',
      queryMessage: typeof timeline.queryMessage === 'string' ? timeline.queryMessage : undefined,
      serviceId: typeof timeline.serviceId === 'string' ? timeline.serviceId : identity.serviceId,
      windowStart: typeof timeline.windowStart === 'string' ? timeline.windowStart : undefined,
      windowEnd: typeof timeline.windowEnd === 'string' ? timeline.windowEnd : undefined,
      generatedAt: typeof timeline.generatedAt === 'string' ? timeline.generatedAt : undefined,
      providerStatus: timeline.providerStatus,
      segments: adaptTimelineSegments(timeline.segments),
    },
    logsQuickView: {
      queryStatus:
        logs.queryStatus === 'no_deployment_window' || logs.queryStatus === 'no_data' ? logs.queryStatus : 'ok',
      queryMessage: typeof logs.queryMessage === 'string' ? logs.queryMessage : undefined,
      serviceId: typeof logs.serviceId === 'string' ? logs.serviceId : identity.serviceId,
      preset: logs.preset === 'warnings' || logs.preset === 'restarts' ? logs.preset : 'errors',
      generatedAt: typeof logs.generatedAt === 'string' ? logs.generatedAt : undefined,
      windowStart: typeof logs.windowStart === 'string' ? logs.windowStart : undefined,
      windowEnd: typeof logs.windowEnd === 'string' ? logs.windowEnd : undefined,
      limit: asNumber(logs.limit) ?? 50,
      returned: asNumber(logs.returned) ?? 0,
      moreAvailable: Boolean(logs.moreAvailable),
      lines: adaptLogsLines(logs.lines),
      providerStatus: logs.providerStatus,
    },
  }
}
