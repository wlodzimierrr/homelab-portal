import { request } from '@/lib/api'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'

export type TimelineStatus = 'healthy' | 'degraded' | 'down' | 'unknown'
export type TimelineWindow = '24h' | '7d'

export interface ServiceHealthTimelineSegment {
  id: string
  startAt: string
  endAt: string
  status: TimelineStatus
  reason?: string
}

export interface ServiceHealthTimeline {
  serviceId: string
  identity: ServiceIdentity
  window: TimelineWindow
  lastRefreshedAt?: string
  segments: ServiceHealthTimelineSegment[]
}

interface ApiSegment {
  id?: string
  start?: string
  end?: string
  status?: string
  reason?: string
}

function normalizeStatus(value?: string): TimelineStatus {
  if (!value) {
    return 'unknown'
  }

  const normalized = value.trim().toLowerCase()
  if (normalized === 'healthy') return 'healthy'
  if (normalized === 'degraded') return 'degraded'
  if (normalized === 'down' || normalized === 'unavailable') return 'down'
  return 'unknown'
}

function asWindow(value?: string): TimelineWindow {
  if (value === '7d') {
    return value
  }
  return '24h'
}

function adaptSegments(segments: ApiSegment[] | undefined): ServiceHealthTimelineSegment[] {
  if (!Array.isArray(segments)) {
    return []
  }

  const adapted = segments
    .map((segment, index): ServiceHealthTimelineSegment | null => {
      const startAt = typeof segment.start === 'string' ? segment.start : ''
      const endAt = typeof segment.end === 'string' ? segment.end : ''
      if (!startAt || !endAt) {
        return null
      }

      return {
        id: typeof segment.id === 'string' && segment.id.trim() ? segment.id : `segment-${index}`,
        startAt,
        endAt,
        status: normalizeStatus(segment.status),
        reason: typeof segment.reason === 'string' ? segment.reason : undefined,
      }
    })
    .filter((segment): segment is ServiceHealthTimelineSegment => segment !== null)

  return adapted.sort((a, b) => new Date(a.startAt).getTime() - new Date(b.startAt).getTime())
}

function resolveIdentity(input: ServiceIdentity | string) {
  if (typeof input === 'string') {
    return createServiceIdentity({ serviceId: input })
  }
  return createServiceIdentity(input)
}

function getTimelineStep(window: TimelineWindow) {
  if (window === '7d') {
    return '1h'
  }
  return '5m'
}

function adaptApi(identity: ServiceIdentity, window: TimelineWindow, payload: ApiSegment[]): ServiceHealthTimeline {
  return {
    serviceId: identity.serviceId,
    identity,
    window: asWindow(window),
    lastRefreshedAt: new Date().toISOString(),
    segments: adaptSegments(payload),
  }
}

async function getFromApi(serviceId: string, window: TimelineWindow) {
  const step = getTimelineStep(window)
  const payload = await request<ApiSegment[]>(
    `/services/${encodeURIComponent(serviceId)}/health/timeline?range=${encodeURIComponent(window)}&step=${encodeURIComponent(step)}`,
  )
  return payload
}

export async function getServiceHealthTimeline(service: ServiceIdentity | string, window: TimelineWindow = '24h') {
  const identity = resolveIdentity(service)
  return adaptApi(identity, window, await getFromApi(identity.serviceId, window))
}
