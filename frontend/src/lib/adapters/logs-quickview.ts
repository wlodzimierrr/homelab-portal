import { request, type MonitoringProviderStatus } from '@/lib/api'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'

export type LogsQuickViewPreset = 'errors' | 'restarts' | 'warnings'
export type LogsQuickViewRange = '15m' | '1h' | '6h' | '24h'

export interface LogsQuickViewLine {
  timestamp: string
  message: string
  labels: Record<string, string>
}

export interface ServiceLogsQuickView {
  serviceId: string
  preset: LogsQuickViewPreset
  range: LogsQuickViewRange
  generatedAt?: string
  limit: number
  returned: number
  moreAvailable: boolean
  nextCursor?: string
  lines: LogsQuickViewLine[]
  providerStatus?: MonitoringProviderStatus
}

interface LogsQuickViewResponse {
  serviceId?: string
  preset?: string
  range?: string
  generatedAt?: string
  limit?: number
  returned?: number
  moreAvailable?: boolean
  nextCursor?: string
  providerStatus?: MonitoringProviderStatus
  lines?: Array<{
    timestamp?: string
    message?: string
    labels?: Record<string, string>
  }>
}

interface LogsQuickViewOptions {
  preset: LogsQuickViewPreset
  range?: LogsQuickViewRange
  limit?: number
  cursor?: string
}

function resolveIdentity(input: ServiceIdentity | string) {
  if (typeof input === 'string') {
    return createServiceIdentity({ serviceId: input })
  }
  return createServiceIdentity(input)
}

function normalizePreset(value: unknown, fallback: LogsQuickViewPreset): LogsQuickViewPreset {
  return value === 'errors' || value === 'restarts' || value === 'warnings' ? value : fallback
}

function normalizeRange(value: unknown, fallback: LogsQuickViewRange): LogsQuickViewRange {
  return value === '15m' || value === '1h' || value === '6h' || value === '24h' ? value : fallback
}

function normalizeLines(lines: LogsQuickViewResponse['lines']): LogsQuickViewLine[] {
  if (!Array.isArray(lines)) {
    return []
  }

  return lines
    .map((line) => {
      const timestamp = typeof line.timestamp === 'string' ? line.timestamp : ''
      const message = typeof line.message === 'string' ? line.message : ''
      const labels =
        line.labels && typeof line.labels === 'object' && !Array.isArray(line.labels) ? line.labels : {}

      if (!timestamp || !message) {
        return null
      }

      return {
        timestamp,
        message,
        labels,
      }
    })
    .filter((line): line is LogsQuickViewLine => line !== null)
}

export async function getServiceLogsQuickView(
  service: ServiceIdentity | string,
  options: LogsQuickViewOptions,
): Promise<ServiceLogsQuickView> {
  const identity = resolveIdentity(service)
  const preset = options.preset
  const range = options.range ?? '1h'
  const limit = typeof options.limit === 'number' && Number.isFinite(options.limit) ? Math.floor(options.limit) : 100

  const params = new URLSearchParams()
  params.set('preset', preset)
  params.set('range', range)
  params.set('limit', String(Math.min(200, Math.max(1, limit))))
  if (options.cursor) {
    params.set('cursor', options.cursor)
  }
  if (identity.namespace) {
    params.set('namespace', identity.namespace)
  }
  if (identity.appLabel) {
    params.set('appLabel', identity.appLabel)
  }

  const payload = await request<LogsQuickViewResponse>(
    `/services/${encodeURIComponent(identity.serviceId)}/logs/quickview?${params.toString()}`,
  )

  return {
    serviceId: typeof payload.serviceId === 'string' ? payload.serviceId : identity.serviceId,
    preset: normalizePreset(payload.preset, preset),
    range: normalizeRange(payload.range, range),
    generatedAt: typeof payload.generatedAt === 'string' ? payload.generatedAt : undefined,
    limit: typeof payload.limit === 'number' && Number.isFinite(payload.limit) ? payload.limit : limit,
    returned: typeof payload.returned === 'number' && Number.isFinite(payload.returned) ? payload.returned : 0,
    moreAvailable: Boolean(payload.moreAvailable),
    nextCursor: typeof payload.nextCursor === 'string' ? payload.nextCursor : undefined,
    lines: normalizeLines(payload.lines),
    providerStatus: payload.providerStatus,
  }
}
