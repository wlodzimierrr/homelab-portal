import { deriveServiceIdentity, getServicesRegistry, type ServiceRegistryItem } from '@/lib/adapters/services'
import { getDeploymentHistory } from '@/lib/adapters/deployments'
import { ApiRequestError, isApiRequestError, request } from '@/lib/api'
import { summarizeDeploymentAlerts } from '@/lib/deployment-alerts'

export type IncidentSeverity = 'info' | 'warning' | 'critical'
export type IncidentStatus = 'active' | 'resolved'

export interface PlatformIncident {
  id: string
  title: string
  severity: IncidentSeverity
  status: IncidentStatus
  startedAt?: string
  source?: string
  serviceId?: string
}

export interface PlatformServiceHealthItem {
  serviceId: string
  serviceName: string
  health: string
  sync: string
  suspicious: boolean
  alertLevel: 'none' | 'warning' | 'critical'
  alertReasons: string[]
}

export interface PlatformHealthSummary {
  totalServices: number
  degradedServices: number
  activeAlerts: number
  activeIncidents: number
}

export interface PlatformHealthOverview {
  summary: PlatformHealthSummary
  unhealthyServices: PlatformServiceHealthItem[]
  incidents: PlatformIncident[]
  warnings: string[]
}

export interface PlatformIncidentFeed {
  incidents: PlatformIncident[]
  warnings: string[]
}

interface IncidentsResponse {
  incidents?: PlatformIncident[]
}

const sampleUrl = new URL('../../../platform-health.sample.json', import.meta.url).toString()
const incidentsMissingStatuses = new Set([404, 405, 501])

type IncidentsApiAvailability = 'unknown' | 'available' | 'unavailable'
let incidentsApiAvailability: IncidentsApiAvailability = 'unknown'

function normalizeSeverity(value?: string): IncidentSeverity {
  if (!value) return 'info'
  const normalized = value.trim().toLowerCase()
  if (normalized === 'critical') return 'critical'
  if (normalized === 'warning') return 'warning'
  return 'info'
}

function normalizeStatus(value?: string): IncidentStatus {
  if (!value) return 'active'
  return value.trim().toLowerCase() === 'resolved' ? 'resolved' : 'active'
}

function normalizeIncidents(input: unknown): PlatformIncident[] {
  if (!Array.isArray(input)) return []

  return input
    .map((raw, index): PlatformIncident | null => {
      if (typeof raw !== 'object' || raw === null) return null
      const item = raw as Record<string, unknown>
      const id = typeof item.id === 'string' && item.id.trim() ? item.id : `incident-${index}`
      const title = typeof item.title === 'string' && item.title.trim() ? item.title : 'Untitled incident'

      return {
        id,
        title,
        severity: normalizeSeverity(typeof item.severity === 'string' ? item.severity : undefined),
        status: normalizeStatus(typeof item.status === 'string' ? item.status : undefined),
        startedAt: typeof item.startedAt === 'string' ? item.startedAt : undefined,
        source: typeof item.source === 'string' ? item.source : undefined,
        serviceId: typeof item.serviceId === 'string' ? item.serviceId : undefined,
      }
    })
    .filter((item): item is PlatformIncident => item !== null)
    .sort((a, b) => {
      const left = a.startedAt ? new Date(a.startedAt).getTime() : 0
      const right = b.startedAt ? new Date(b.startedAt).getTime() : 0
      return right - left
    })
}

async function getIncidentsFromApi() {
  if (incidentsApiAvailability === 'unavailable') {
    throw new ApiRequestError('Incidents endpoint is not available in this backend.', 404)
  }

  const response = await request<IncidentsResponse>('/monitoring/incidents')
  incidentsApiAvailability = 'available'
  return normalizeIncidents(response.incidents)
}

async function getIncidentsFromSample() {
  const response = await fetch(sampleUrl)
  if (!response.ok) {
    throw new Error('Failed to load platform health sample incidents.')
  }

  const payload = (await response.json()) as IncidentsResponse
  return normalizeIncidents(payload.incidents)
}

async function buildServiceHealthItems(services: ServiceRegistryItem[]): Promise<PlatformServiceHealthItem[]> {
  return Promise.all(
    services.map(async (service) => {
      const identity = deriveServiceIdentity(service)
      const baseHealth = service.health
      const baseSync = service.sync

      try {
        const deployments = await getDeploymentHistory(identity, { limit: 3 })
        const summary = summarizeDeploymentAlerts(
          deployments.map((item) => ({
            outcome: item.outcome,
            regressionScore: item.regressionScore,
            errorRateDeltaPct: item.errorRatePct.delta,
            latencyDeltaMs: item.p95LatencyMs.delta,
            availabilityDeltaPct: item.availabilityPct.delta,
          })),
        )

        return {
          serviceId: identity.serviceId,
          serviceName: identity.serviceName,
          health: baseHealth === 'degraded' || summary.suspicious ? 'degraded' : baseHealth,
          sync: baseSync,
          suspicious: summary.suspicious,
          alertLevel: summary.level,
          alertReasons: summary.reasons,
        }
      } catch {
        return {
          serviceId: identity.serviceId,
          serviceName: identity.serviceName,
          health: baseHealth,
          sync: baseSync,
          suspicious: false,
          alertLevel: 'none' as const,
          alertReasons: [],
        }
      }
    }),
  )
}

export async function getPlatformHealthOverview(): Promise<PlatformHealthOverview> {
  const warnings: string[] = []

  const servicesResult = await Promise.allSettled([getServicesRegistry()])
  const services = servicesResult[0].status === 'fulfilled' ? servicesResult[0].value : []
  if (servicesResult[0].status === 'rejected') {
    warnings.push('Service registry source unavailable.')
  }

  const [healthItemsResult, incidentsApiResult] = await Promise.allSettled([
    buildServiceHealthItems(services),
    getIncidentsFromApi(),
  ])

  let serviceHealthItems: PlatformServiceHealthItem[] = []
  if (healthItemsResult.status === 'fulfilled') {
    serviceHealthItems = healthItemsResult.value
  } else {
    warnings.push('Deployment alert signals unavailable for one or more services.')
  }

  let incidents: PlatformIncident[] = []
  if (incidentsApiResult.status === 'fulfilled') {
    incidents = incidentsApiResult.value
  } else {
    if (
      isApiRequestError(incidentsApiResult.reason) &&
      incidentsMissingStatuses.has(incidentsApiResult.reason.status)
    ) {
      incidentsApiAvailability = 'unavailable'
    }
    warnings.push('Incidents API unavailable, using sample incident feed.')
    try {
      incidents = await getIncidentsFromSample()
    } catch {
      warnings.push('Sample incidents unavailable.')
    }
  }

  const unhealthyServices = serviceHealthItems
    .filter((item) => item.health === 'degraded' || item.sync === 'out_of_sync' || item.suspicious)
    .sort((a, b) => {
      const priority = { critical: 2, warning: 1, none: 0 }
      if (priority[b.alertLevel] !== priority[a.alertLevel]) {
        return priority[b.alertLevel] - priority[a.alertLevel]
      }
      return a.serviceName.localeCompare(b.serviceName)
    })

  const summary: PlatformHealthSummary = {
    totalServices: serviceHealthItems.length,
    degradedServices: serviceHealthItems.filter((item) => item.health === 'degraded').length,
    activeAlerts: serviceHealthItems.filter((item) => item.suspicious).length,
    activeIncidents: incidents.filter((incident) => incident.status === 'active').length,
  }

  return {
    summary,
    unhealthyServices,
    incidents,
    warnings,
  }
}

export async function getPlatformIncidentFeed(): Promise<PlatformIncidentFeed> {
  const warnings: string[] = []

  try {
    const incidents = await getIncidentsFromApi()
    return { incidents, warnings }
  } catch (error) {
    if (isApiRequestError(error) && incidentsMissingStatuses.has(error.status)) {
      incidentsApiAvailability = 'unavailable'
    }
    warnings.push('Incidents API unavailable, using sample incident feed.')
  }

  try {
    const incidents = await getIncidentsFromSample()
    return { incidents, warnings }
  } catch {
    warnings.push('Sample incidents unavailable.')
    return { incidents: [], warnings }
  }
}
