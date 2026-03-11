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

export interface ServiceMetricTrendPoint {
  timestamp: string
  value: number
}

export interface ServiceMetricTrendSeries {
  queryStatus: 'ok' | 'no_data'
  queryMessage?: string
  querySource?: 'app_metrics' | 'traefik_fallback'
  latestValue?: number
  pointCount: number
  points: ServiceMetricTrendPoint[]
}

export interface ServiceMetricsTrends {
  serviceId: string
  identity?: ServiceIdentity
  range: ServiceMetricsRange
  windowStart?: string
  windowEnd?: string
  generatedAt?: string
  providerStatus?: MonitoringProviderStatus
  p95LatencyMs: ServiceMetricTrendSeries
  errorRatePct: ServiceMetricTrendSeries
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

interface ServiceMetricTrendSeriesResponse {
  queryStatus?: 'ok' | 'no_data'
  queryMessage?: string
  querySource?: 'app_metrics' | 'traefik_fallback'
  latestValue?: number
  pointCount?: number
  points?: Array<{
    timestamp?: string
    value?: number
  }>
}

interface ServiceMetricsTrendsResponse {
  serviceId?: string
  range?: ServiceMetricsRange
  windowStart?: string
  windowEnd?: string
  generatedAt?: string
  providerStatus?: MonitoringProviderStatus
  p95LatencyMs?: ServiceMetricTrendSeriesResponse
  errorRatePct?: ServiceMetricTrendSeriesResponse
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

function emptyTrendSeries(): ServiceMetricTrendSeries {
  return {
    queryStatus: 'no_data',
    pointCount: 0,
    points: [],
  }
}

function normalizeTrendPoints(points: ServiceMetricTrendSeriesResponse['points']): ServiceMetricTrendPoint[] {
  if (!Array.isArray(points)) {
    return []
  }

  return points
    .map((point) => {
      const timestamp = typeof point.timestamp === 'string' ? point.timestamp : ''
      const value = isFiniteNumber(point.value) ? point.value : undefined
      if (!timestamp || value === undefined) {
        return null
      }
      return { timestamp, value }
    })
    .filter((point): point is ServiceMetricTrendPoint => point !== null)
}

function adaptTrendSeries(input: ServiceMetricTrendSeriesResponse | undefined): ServiceMetricTrendSeries {
  if (!input) {
    return emptyTrendSeries()
  }

  const points = normalizeTrendPoints(input.points)
  const pointCount =
    typeof input.pointCount === 'number' && Number.isFinite(input.pointCount) ? input.pointCount : points.length

  return {
    queryStatus: input.queryStatus === 'ok' ? 'ok' : 'no_data',
    queryMessage: typeof input.queryMessage === 'string' ? input.queryMessage : undefined,
    querySource:
      input.querySource === 'app_metrics' || input.querySource === 'traefik_fallback'
        ? input.querySource
        : undefined,
    latestValue: isFiniteNumber(input.latestValue) ? input.latestValue : points.at(-1)?.value,
    pointCount,
    points,
  }
}

function adaptTrends(
  identity: ServiceIdentity,
  range: ServiceMetricsRange,
  payload: ServiceMetricsTrendsResponse,
): ServiceMetricsTrends {
  return {
    serviceId: identity.serviceId,
    identity,
    range,
    windowStart: typeof payload.windowStart === 'string' ? payload.windowStart : undefined,
    windowEnd: typeof payload.windowEnd === 'string' ? payload.windowEnd : undefined,
    generatedAt: typeof payload.generatedAt === 'string' ? payload.generatedAt : undefined,
    providerStatus: payload.providerStatus,
    p95LatencyMs: adaptTrendSeries(payload.p95LatencyMs),
    errorRatePct: adaptTrendSeries(payload.errorRatePct),
  }
}

async function getMetricsFromApi(serviceId: string, range: ServiceMetricsRange) {
  const encodedServiceId = encodeURIComponent(serviceId)
  const payload = await request<ServiceMetricsSummaryResponse>(
    `/services/${encodedServiceId}/metrics/summary?range=${encodeURIComponent(range)}`,
  )
  return payload
}

async function getMetricTrendsFromApi(serviceId: string, range: ServiceMetricsRange) {
  const encodedServiceId = encodeURIComponent(serviceId)
  return request<ServiceMetricsTrendsResponse>(
    `/services/${encodedServiceId}/metrics/trends?range=${encodeURIComponent(range)}`,
  )
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

export function createEmptyServiceMetricsTrends(
  service: ServiceIdentity | string,
  range: ServiceMetricsRange = '24h',
): ServiceMetricsTrends {
  const identity = resolveIdentity(service)
  return {
    serviceId: identity.serviceId,
    identity,
    range,
    p95LatencyMs: emptyTrendSeries(),
    errorRatePct: emptyTrendSeries(),
  }
}

export async function getServiceMetricsSummary(
  service: ServiceIdentity | string,
  range: ServiceMetricsRange = '24h',
): Promise<ServiceMetricsSummary> {
  const identity = resolveIdentity(service)
  return adaptSummary(identity, range, await getMetricsFromApi(identity.serviceId, range))
}

export async function getServiceMetricsTrends(
  service: ServiceIdentity | string,
  range: ServiceMetricsRange = '24h',
): Promise<ServiceMetricsTrends> {
  const identity = resolveIdentity(service)
  return adaptTrends(identity, range, await getMetricTrendsFromApi(identity.serviceId, range))
}
