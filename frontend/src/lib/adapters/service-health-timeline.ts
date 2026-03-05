import { request } from '@/lib/api'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'

export type TimelineStatus = 'healthy' | 'degraded' | 'down' | 'unknown'
export type TimelineWindow = '6h' | '24h' | '7d'

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
  startAt?: string
  endAt?: string
  status?: string
  reason?: string
}

interface ApiPayload {
  serviceId?: string
  window?: string
  lastRefreshedAt?: string
  segments?: ApiSegment[]
}

interface SampleEntry {
  serviceId?: string
  lastRefreshedAt?: string
  timelines?: Partial<Record<TimelineWindow, ApiSegment[]>>
}

interface SamplePayload {
  services?: SampleEntry[]
}

const sampleUrl = new URL('../../../service-health-timeline.sample.json', import.meta.url).toString()

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
  if (value === '6h' || value === '7d') {
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
      const startAt = typeof segment.startAt === 'string' ? segment.startAt : ''
      const endAt = typeof segment.endAt === 'string' ? segment.endAt : ''
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

function emptyTimeline(identity: ServiceIdentity, window: TimelineWindow): ServiceHealthTimeline {
  return {
    serviceId: identity.serviceId,
    identity,
    window,
    segments: [],
  }
}

function adaptApi(identity: ServiceIdentity, window: TimelineWindow, payload: ApiPayload): ServiceHealthTimeline {
  return {
    serviceId: typeof payload.serviceId === 'string' ? payload.serviceId : identity.serviceId,
    identity,
    window: asWindow(payload.window ?? window),
    lastRefreshedAt: typeof payload.lastRefreshedAt === 'string' ? payload.lastRefreshedAt : undefined,
    segments: adaptSegments(payload.segments),
  }
}

async function getFromApi(serviceId: string, window: TimelineWindow) {
  const payload = await request<ApiPayload>(
    `/services/${encodeURIComponent(serviceId)}/health-timeline?range=${encodeURIComponent(window)}`,
  )
  return payload
}

async function getFromSample(serviceId: string, window: TimelineWindow) {
  const response = await fetch(sampleUrl)
  if (!response.ok) {
    throw new Error('Failed to load service health timeline sample data.')
  }

  const payload = (await response.json()) as SamplePayload
  const match = payload.services?.find((entry) => {
    if (typeof entry.serviceId !== 'string') return false
    return entry.serviceId.trim().toLowerCase() === serviceId.trim().toLowerCase()
  })

  if (!match) {
    return undefined
  }

  return {
    serviceId,
    window,
    lastRefreshedAt: typeof match.lastRefreshedAt === 'string' ? match.lastRefreshedAt : undefined,
    segments: adaptSegments(match.timelines?.[window]),
  }
}

export async function getServiceHealthTimeline(service: ServiceIdentity | string, window: TimelineWindow = '24h') {
  const identity = resolveIdentity(service)

  try {
    return adaptApi(identity, window, await getFromApi(identity.serviceId, window))
  } catch {
    try {
      const fromSample = await getFromSample(identity.serviceId, window)
      if (!fromSample) {
        return emptyTimeline(identity, window)
      }
      return {
        ...fromSample,
        identity,
      }
    } catch {
      return emptyTimeline(identity, window)
    }
  }
}
