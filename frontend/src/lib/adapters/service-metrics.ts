import { request } from '@/lib/api'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'

export interface ServiceMetricsSummary {
  serviceId: string
  identity?: ServiceIdentity
  uptime24hPct?: number
  uptime7dPct?: number
  p95LatencyMs?: number
  errorRatePct?: number
  restartCount?: number
  lastRefreshedAt?: string
}

interface ServiceMetricsSummaryResponse {
  serviceId?: string
  uptimePct?: number
  uptime24hPct?: number
  uptime7dPct?: number
  p95LatencyMs?: number
  errorRatePct?: number
  restartCount?: number
  lastRefreshedAt?: string
}

interface ServiceMetricsSamplePayload {
  services?: ServiceMetricsSummaryResponse[]
}

const serviceMetricsSampleUrl = new URL('../../../service-metrics.sample.json', import.meta.url).toString()

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function resolveIdentity(input: ServiceIdentity | string) {
  if (typeof input === 'string') {
    return createServiceIdentity({ serviceId: input })
  }
  return createServiceIdentity(input)
}

function adaptSummary(identity: ServiceIdentity, payload: ServiceMetricsSummaryResponse): ServiceMetricsSummary {
  return {
    serviceId: identity.serviceId,
    identity,
    uptime24hPct: isFiniteNumber(payload.uptime24hPct)
      ? payload.uptime24hPct
      : isFiniteNumber(payload.uptimePct)
        ? payload.uptimePct
        : undefined,
    uptime7dPct: isFiniteNumber(payload.uptime7dPct) ? payload.uptime7dPct : undefined,
    p95LatencyMs: isFiniteNumber(payload.p95LatencyMs) ? payload.p95LatencyMs : undefined,
    errorRatePct: isFiniteNumber(payload.errorRatePct) ? payload.errorRatePct : undefined,
    restartCount: isFiniteNumber(payload.restartCount) ? payload.restartCount : undefined,
    lastRefreshedAt: typeof payload.lastRefreshedAt === 'string' ? payload.lastRefreshedAt : undefined,
  }
}

async function getMetricsFromApi(serviceId: string) {
  const encodedServiceId = encodeURIComponent(serviceId)
  const payload = await request<ServiceMetricsSummaryResponse>(`/services/${encodedServiceId}/metrics-summary`)
  return payload
}

async function getMetricsFromSample(serviceId: string) {
  const response = await fetch(serviceMetricsSampleUrl)
  if (!response.ok) {
    throw new Error('Failed to load service metrics sample data.')
  }

  const payload = (await response.json()) as ServiceMetricsSamplePayload
  if (!Array.isArray(payload.services)) {
    return undefined
  }

  const match = payload.services.find((entry) => {
    if (typeof entry.serviceId !== 'string') {
      return false
    }
    return entry.serviceId.trim().toLowerCase() === serviceId.trim().toLowerCase()
  })

  if (!match) {
    return undefined
  }

  return match
}

export async function getServiceMetricsSummary(service: ServiceIdentity | string): Promise<ServiceMetricsSummary> {
  const identity = resolveIdentity(service)

  try {
    return adaptSummary(identity, await getMetricsFromApi(identity.serviceId))
  } catch {
    try {
      const fromSample = await getMetricsFromSample(identity.serviceId)
      return fromSample ? adaptSummary(identity, fromSample) : { serviceId: identity.serviceId, identity }
    } catch {
      return { serviceId: identity.serviceId, identity }
    }
  }
}
