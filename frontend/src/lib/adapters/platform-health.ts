import { deriveServiceIdentity, getServicesRegistry, type ServiceRegistryItem } from '@/lib/adapters/services'
import { getDeploymentHistory } from '@/lib/adapters/deployments'
import {
  ApiRequestError,
  getProjectCatalogDiagnostics,
  getMonitoringProvidersDiagnostics,
  getServiceRegistryDiagnostics,
  isApiRequestError,
  request,
  type MonitoringProviderStatus,
  type RegistryFreshness,
} from '@/lib/api'
import { summarizeDeploymentAlerts } from '@/lib/deployment-alerts'

export type IncidentSeverity = 'info' | 'warning' | 'critical'
export type IncidentStatus = 'active' | 'resolved'

export interface PlatformIncident {
  id: string
  title: string
  description?: string
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

export interface PlatformCatalogStatus {
  key: 'projects' | 'services'
  label: string
  freshness: RegistryFreshness
}

export interface PlatformHealthOverview {
  summary: PlatformHealthSummary
  unhealthyServices: PlatformServiceHealthItem[]
  incidents: PlatformIncident[]
  providers: MonitoringProviderStatus[]
  catalogs: PlatformCatalogStatus[]
  warnings: string[]
}

export interface PlatformIncidentFeed {
  incidents: PlatformIncident[]
  warnings: string[]
}

interface ActiveAlertItem {
  id?: string
  severity?: string
  title?: string
  description?: string
  startsAt?: string
  labels?: Record<string, string>
  serviceId?: string
  env?: string
}

interface ActiveAlertsResponse {
  alerts?: ActiveAlertItem[]
  providerStatus?: MonitoringProviderStatus
}

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

function normalizeActiveAlerts(input: unknown): PlatformIncident[] {
  if (!Array.isArray(input)) return []

  return input
    .map((raw, index): PlatformIncident | null => {
      if (typeof raw !== 'object' || raw === null) return null
      const item = raw as ActiveAlertItem
      const id = typeof item.id === 'string' && item.id.trim() ? item.id : `alert-${index}`
      const title = typeof item.title === 'string' && item.title.trim() ? item.title : 'Untitled alert'

      return {
        id,
        title,
        description: typeof item.description === 'string' ? item.description : undefined,
        severity: normalizeSeverity(item.severity),
        status: 'active',
        startedAt: typeof item.startsAt === 'string' ? item.startsAt : undefined,
        source: 'alertmanager',
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
    throw new ApiRequestError('Alerts endpoint is not available in this backend.', 404)
  }

  const response = await request<ActiveAlertsResponse>('/alerts/active')
  incidentsApiAvailability = 'available'
  return {
    incidents: normalizeActiveAlerts(response.alerts ?? []),
    providerStatus: response.providerStatus,
  }
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

function addCatalogWarning(warnings: string[], label: string, freshness: RegistryFreshness) {
  if (freshness.state === 'warning') {
    warnings.push(`${label} catalog is warning and will become stale soon without another sync.`)
  } else if (freshness.state === 'stale') {
    warnings.push(`${label} catalog is stale.`)
  } else if (freshness.state === 'empty') {
    warnings.push(`${label} catalog is reachable but empty.`)
  }
}

export async function getPlatformHealthOverview(): Promise<PlatformHealthOverview> {
  const warnings: string[] = []

  const servicesResult = await Promise.allSettled([getServicesRegistry()])
  const services = servicesResult[0].status === 'fulfilled' ? servicesResult[0].value : []
  if (servicesResult[0].status === 'rejected') {
    warnings.push('Service registry source unavailable.')
  }

  const [healthItemsResult, incidentsApiResult, providersResult, projectDiagnosticsResult, serviceDiagnosticsResult] =
    await Promise.allSettled([
    buildServiceHealthItems(services),
    getIncidentsFromApi(),
    getMonitoringProvidersDiagnostics(),
    getProjectCatalogDiagnostics(),
    getServiceRegistryDiagnostics(),
  ])

  let serviceHealthItems: PlatformServiceHealthItem[] = []
  if (healthItemsResult.status === 'fulfilled') {
    serviceHealthItems = healthItemsResult.value
  } else {
    warnings.push('Deployment alert signals unavailable for one or more services.')
  }

  let incidents: PlatformIncident[] = []
  let providers: MonitoringProviderStatus[] = []
  if (incidentsApiResult.status === 'fulfilled') {
    incidents = incidentsApiResult.value.incidents
    if (incidentsApiResult.value.providerStatus && incidentsApiResult.value.providerStatus.status !== 'healthy') {
      warnings.push(
        `Alerts provider is ${incidentsApiResult.value.providerStatus.status.replace(/_/g, ' ')}.`,
      )
    }
  } else {
    if (
      isApiRequestError(incidentsApiResult.reason) &&
      incidentsMissingStatuses.has(incidentsApiResult.reason.status)
    ) {
      incidentsApiAvailability = 'unavailable'
    }
    warnings.push('Active alerts feed unavailable from /api/alerts/active.')
  }

  if (providersResult.status === 'fulfilled') {
    providers = providersResult.value.providers
    const degradedProviders = providers.filter((provider) => provider.status !== 'healthy')
    for (const provider of degradedProviders) {
      warnings.push(
        `${provider.provider} is ${provider.status.replace(/_/g, ' ')}${provider.error ? `: ${provider.error}` : '.'}`,
      )
    }
  } else {
    warnings.push('Monitoring provider diagnostics are unavailable.')
  }

  const catalogs: PlatformCatalogStatus[] = []
  if (projectDiagnosticsResult.status === 'fulfilled') {
    catalogs.push({
      key: 'projects',
      label: 'Projects',
      freshness: projectDiagnosticsResult.value.freshness,
    })
    addCatalogWarning(warnings, 'Projects', projectDiagnosticsResult.value.freshness)
  } else {
    warnings.push('Project catalog diagnostics are unavailable.')
  }

  if (serviceDiagnosticsResult.status === 'fulfilled') {
    catalogs.push({
      key: 'services',
      label: 'Services',
      freshness: serviceDiagnosticsResult.value.freshness,
    })
    addCatalogWarning(warnings, 'Services', serviceDiagnosticsResult.value.freshness)
  } else {
    warnings.push('Service catalog diagnostics are unavailable.')
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
    providers,
    catalogs,
    warnings: [...new Set(warnings)],
  }
}

export async function getPlatformIncidentFeed(): Promise<PlatformIncidentFeed> {
  const warnings: string[] = []

  try {
    const result = await getIncidentsFromApi()
    return { incidents: result.incidents, warnings }
  } catch (error) {
    if (isApiRequestError(error) && incidentsMissingStatuses.has(error.status)) {
      incidentsApiAvailability = 'unavailable'
    }
    warnings.push('Active alerts feed unavailable from /api/alerts/active.')
    return { incidents: [], warnings }
  }
}
