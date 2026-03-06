import { request, type MonitoringProviderStatus } from '@/lib/api'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'

export type ServiceMetricsRange = '1h' | '24h' | '7d'

interface ServiceMetricsNoData {
  uptimePct: boolean
  p95LatencyMs: boolean
  errorRatePct: boolean
  restartCount: boolean
}

export interface ServiceMetricsSummary {
  serviceId: string
  identity?: ServiceIdentity
  range: ServiceMetricsRange
  uptimePct?: number
  p95LatencyMs?: number
  errorRatePct?: number
  restartCount?: number
  windowStart?: string
  windowEnd?: string
  generatedAt?: string
  noData: ServiceMetricsNoData
  providerStatus?: MonitoringProviderStatus
}

interface ServiceMetricsSummaryResponse {
  serviceId?: string
  uptimePct?: number
  p95LatencyMs?: number
  errorRatePct?: number
  restartCount?: number
  windowStart?: string
  windowEnd?: string
  generatedAt?: string
  noData?: Partial<ServiceMetricsNoData>
  providerStatus?: MonitoringProviderStatus
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function resolveIdentity(input: ServiceIdentity | string) {
  if (typeof input === 'string') {
    return createServiceIdentity({ serviceId: input })
  }
  return createServiceIdentity(input)
}

function emptyNoData(): ServiceMetricsNoData {
  return {
    uptimePct: true,
    p95LatencyMs: true,
    errorRatePct: true,
    restartCount: true,
  }
}

function normalizeNoData(payload: ServiceMetricsSummaryResponse): ServiceMetricsNoData {
  const fromApi = payload.noData ?? {}

  const uptimeNoData = typeof fromApi.uptimePct === 'boolean' ? fromApi.uptimePct : !isFiniteNumber(payload.uptimePct)
  const latencyNoData =
    typeof fromApi.p95LatencyMs === 'boolean' ? fromApi.p95LatencyMs : !isFiniteNumber(payload.p95LatencyMs)
  const errorNoData =
    typeof fromApi.errorRatePct === 'boolean' ? fromApi.errorRatePct : !isFiniteNumber(payload.errorRatePct)
  const restartNoData =
    typeof fromApi.restartCount === 'boolean' ? fromApi.restartCount : !isFiniteNumber(payload.restartCount)

  return {
    uptimePct: uptimeNoData,
    p95LatencyMs: latencyNoData,
    errorRatePct: errorNoData,
    restartCount: restartNoData,
  }
}

function adaptSummary(
  identity: ServiceIdentity,
  range: ServiceMetricsRange,
  payload: ServiceMetricsSummaryResponse,
): ServiceMetricsSummary {
  return {
    serviceId: identity.serviceId,
    identity,
    range,
    uptimePct: isFiniteNumber(payload.uptimePct) ? payload.uptimePct : undefined,
    p95LatencyMs: isFiniteNumber(payload.p95LatencyMs) ? payload.p95LatencyMs : undefined,
    errorRatePct: isFiniteNumber(payload.errorRatePct) ? payload.errorRatePct : undefined,
    restartCount: isFiniteNumber(payload.restartCount) ? payload.restartCount : undefined,
    windowStart: typeof payload.windowStart === 'string' ? payload.windowStart : undefined,
    windowEnd: typeof payload.windowEnd === 'string' ? payload.windowEnd : undefined,
    generatedAt: typeof payload.generatedAt === 'string' ? payload.generatedAt : undefined,
    noData: normalizeNoData(payload),
    providerStatus: payload.providerStatus,
  }
}

async function getMetricsFromApi(serviceId: string, range: ServiceMetricsRange) {
  const encodedServiceId = encodeURIComponent(serviceId)
  const payload = await request<ServiceMetricsSummaryResponse>(
    `/services/${encodedServiceId}/metrics/summary?range=${encodeURIComponent(range)}`,
  )
  return payload
}

export function createEmptyServiceMetricsSummary(
  service: ServiceIdentity | string,
  range: ServiceMetricsRange = '24h',
): ServiceMetricsSummary {
  const identity = resolveIdentity(service)
  return {
    serviceId: identity.serviceId,
    identity,
    range,
    noData: emptyNoData(),
  }
}

export async function getServiceMetricsSummary(
  service: ServiceIdentity | string,
  range: ServiceMetricsRange = '24h',
): Promise<ServiceMetricsSummary> {
  const identity = resolveIdentity(service)
  return adaptSummary(identity, range, await getMetricsFromApi(identity.serviceId, range))
}
