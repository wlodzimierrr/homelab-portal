import {
  getProjects,
  getService,
  getServiceDeployments,
  getServices,
  isApiRequestError,
  type Project,
  type ServiceRegistryApiRow,
} from '@/lib/api'
import { getServiceMetricsSummary } from '@/lib/adapters/service-metrics'
import { createServiceIdentity, normalizeServiceId, parseNamespaceFromInternalUrl, type ServiceIdentity } from '@/lib/service-identity'

export type ServiceHealth = 'healthy' | 'degraded' | 'unknown'
export type ServiceSync = 'synced' | 'out_of_sync' | 'unknown'

export interface ServiceRegistryItem {
  id: string
  name: string
  environments: string[]
  health: ServiceHealth
  sync: ServiceSync
  uptime24hPct?: number
  uptime7dPct?: number
  metricsLastRefreshedAt?: string
  publicUrl?: string
  internalUrls?: string[]
  lastDeployAt?: string
  namespace?: string
  appLabel?: string
  argoAppName?: string
}

const serviceFallbackStatuses = new Set([404, 405, 501])

function normalizeHealthStatus(value?: string): ServiceHealth {
  if (!value) {
    return 'unknown'
  }

  const normalized = value.trim().toLowerCase()
  if (normalized === 'healthy') {
    return 'healthy'
  }
  if (normalized === 'degraded' || normalized === 'unhealthy') {
    return 'degraded'
  }
  return 'unknown'
}

function normalizeSyncStatus(value?: string): ServiceSync {
  if (!value) {
    return 'unknown'
  }

  const normalized = value.trim().toLowerCase()
  if (normalized === 'synced') {
    return 'synced'
  }
  if (normalized === 'out_of_sync' || normalized === 'out-of-sync' || normalized === 'outofsync') {
    return 'out_of_sync'
  }
  return 'unknown'
}

function adaptProjectsToServices(projects: Project[]): ServiceRegistryItem[] {
  const grouped = new Map<string, ServiceRegistryItem>()

  for (const project of projects) {
    const canonicalId = normalizeServiceId(project.name) || normalizeServiceId(project.id) || project.id.trim().toLowerCase()
    const key = canonicalId
    const current = grouped.get(key)
    const nextHealth = normalizeHealthStatus(project.health)
    const nextSync = normalizeSyncStatus(project.sync)

    if (!current) {
      const inferredNamespace = parseNamespaceFromInternalUrl(project.internalUrl) ?? 'default'
      grouped.set(key, {
        id: canonicalId,
        name: project.name,
        environments: [project.environment],
        health: nextHealth,
        sync: nextSync,
        publicUrl: project.publicUrl,
        internalUrls: project.internalUrl ? [project.internalUrl] : undefined,
        lastDeployAt: project.lastDeployAt,
        namespace: inferredNamespace,
        appLabel: canonicalId,
        argoAppName: `${canonicalId}-${project.environment}`,
      })
      continue
    }

    if (!current.environments.includes(project.environment)) {
      current.environments.push(project.environment)
      current.environments.sort((a, b) => a.localeCompare(b))
    }

    if (current.health === 'unknown' && nextHealth !== 'unknown') {
      current.health = nextHealth
    }

    if (current.sync === 'unknown' && nextSync !== 'unknown') {
      current.sync = nextSync
    }

    if (!current.publicUrl && project.publicUrl) {
      current.publicUrl = project.publicUrl
    }

    if (project.internalUrl) {
      const list = current.internalUrls ?? []
      if (!list.includes(project.internalUrl)) {
        list.push(project.internalUrl)
      }
      current.internalUrls = list
      if (!current.namespace) {
        current.namespace = parseNamespaceFromInternalUrl(project.internalUrl) ?? current.namespace
      }
    }

    if (!current.lastDeployAt && project.lastDeployAt) {
      current.lastDeployAt = project.lastDeployAt
    }
  }

  return [...grouped.values()].sort((a, b) => a.name.localeCompare(b.name))
}

function adaptApiServices(rows: ServiceRegistryApiRow[]): ServiceRegistryItem[] {
  const grouped = new Map<string, ServiceRegistryItem>()

  for (const row of rows) {
    const current = grouped.get(row.serviceId)
    if (!current) {
      grouped.set(row.serviceId, {
        id: row.serviceId,
        name: row.serviceName,
        environments: [row.env],
        health: 'unknown',
        sync: 'unknown',
        namespace: row.namespace,
        appLabel: row.appLabel,
        argoAppName: row.argoAppName,
      })
      continue
    }

    if (!current.environments.includes(row.env)) {
      current.environments.push(row.env)
      current.environments.sort((a, b) => a.localeCompare(b))
    }
  }

  return [...grouped.values()].sort((a, b) => a.name.localeCompare(b.name))
}

function mergeProjectMetadata(services: ServiceRegistryItem[], projects: Project[]): ServiceRegistryItem[] {
  const byId = new Map<string, ServiceRegistryItem>()
  for (const service of services) {
    byId.set(service.id, {
      ...service,
      environments: [...service.environments],
      internalUrls: service.internalUrls ? [...service.internalUrls] : undefined,
    })
  }

  for (const project of projects) {
    const canonicalId =
      normalizeServiceId(project.name) ||
      normalizeServiceId(project.id) ||
      project.id.trim().toLowerCase()
    const service = byId.get(canonicalId)
    if (!service) {
      continue
    }

    const nextHealth = normalizeHealthStatus(project.health)
    const nextSync = normalizeSyncStatus(project.sync)

    if (service.health === 'unknown' && nextHealth !== 'unknown') {
      service.health = nextHealth
    }

    if (service.sync === 'unknown' && nextSync !== 'unknown') {
      service.sync = nextSync
    }

    if (!service.publicUrl && project.publicUrl) {
      service.publicUrl = project.publicUrl
    }

    if (project.internalUrl) {
      const urls = service.internalUrls ?? []
      if (!urls.includes(project.internalUrl)) {
        urls.push(project.internalUrl)
      }
      service.internalUrls = urls
    }

    if (!service.lastDeployAt && project.lastDeployAt) {
      service.lastDeployAt = project.lastDeployAt
    }
  }

  return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name))
}

function cloneService(service: ServiceRegistryItem): ServiceRegistryItem {
  return {
    ...service,
    environments: [...service.environments],
    internalUrls: service.internalUrls ? [...service.internalUrls] : undefined,
  }
}

async function enrichServicesWithLiveMetadata(services: ServiceRegistryItem[]) {
  const enriched = await Promise.all(
    services.map(async (service) => {
      const next = cloneService(service)
      const [detailResult, deploymentsResult, metrics24Result, metrics7Result] = await Promise.allSettled([
        getService(service.id),
        getServiceDeployments(service.id),
        getServiceMetricsSummary(service.id, '24h'),
        getServiceMetricsSummary(service.id, '7d'),
      ])

      if (detailResult.status === 'fulfilled') {
        const details = detailResult.value
        const nextHealth = normalizeHealthStatus(details.health)
        const nextSync = normalizeSyncStatus(details.sync)
        if (nextHealth !== 'unknown') {
          next.health = nextHealth
        }
        if (nextSync !== 'unknown') {
          next.sync = nextSync
        }
        if (!next.namespace && details.namespace) {
          next.namespace = details.namespace
        }
        if (!next.appLabel && details.appLabel) {
          next.appLabel = details.appLabel
        }
        if (!next.argoAppName && details.argoAppName) {
          next.argoAppName = details.argoAppName
        }
      }

      if (deploymentsResult.status === 'fulfilled') {
        const latest = deploymentsResult.value.deployments.find((deployment) => deployment.deployedAt || deployment.version || deployment.status)
        if (latest?.deployedAt) {
          next.lastDeployAt = latest.deployedAt
        }
      }

      if (metrics24Result.status === 'fulfilled') {
        next.uptime24hPct = metrics24Result.value.uptimePct
        next.metricsLastRefreshedAt = metrics24Result.value.generatedAt ?? next.metricsLastRefreshedAt
      }

      if (metrics7Result.status === 'fulfilled') {
        next.uptime7dPct = metrics7Result.value.uptimePct
        next.metricsLastRefreshedAt = next.metricsLastRefreshedAt ?? metrics7Result.value.generatedAt
      }

      return next
    }),
  )

  return enriched.sort((a, b) => a.name.localeCompare(b.name))
}

export async function getServicesRegistry() {
  try {
    const servicesResponse = await getServices()
    const liveServices = adaptApiServices(servicesResponse.services)
    try {
      const projectsResponse = await getProjects()
      return enrichServicesWithLiveMetadata(mergeProjectMetadata(liveServices, projectsResponse.projects))
    } catch {
      return enrichServicesWithLiveMetadata(liveServices)
    }
  } catch (error) {
    if (!isApiRequestError(error) || !serviceFallbackStatuses.has(error.status)) {
      throw error instanceof Error ? error : new Error('Failed to load services from live API.')
    }
  }

  try {
    const response = await getProjects()
    const fromApi = adaptProjectsToServices(response.projects)
    if (fromApi.length > 0) {
      return enrichServicesWithLiveMetadata(fromApi)
    }
    throw new Error('Live services API is unavailable and projects fallback returned no services.')
  } catch (error) {
    throw error instanceof Error ? error : new Error('Failed to load services from API.')
  }
}

export function deriveServiceIdentity(service: ServiceRegistryItem, env?: string): ServiceIdentity {
  return createServiceIdentity({
    serviceId: service.id,
    serviceName: service.name,
    namespace: service.namespace,
    env: env ?? service.environments[0],
    appLabel: service.appLabel,
    argoAppName: service.argoAppName,
  })
}

export async function getServiceIdentity(serviceId: string, env?: string): Promise<ServiceIdentity> {
  const services = await getServicesRegistry()
  const match = services.find((service) => service.id.trim().toLowerCase() === serviceId.trim().toLowerCase())
  if (!match) {
    return createServiceIdentity({ serviceId, env })
  }
  return deriveServiceIdentity(match, env)
}
