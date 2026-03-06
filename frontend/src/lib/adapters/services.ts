import { getProjects, getServices, type Project, type ServiceRegistryApiRow } from '@/lib/api'
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

export async function getServicesRegistry() {
  try {
    const servicesResponse = await getServices()
    const liveRows = adaptApiServices(servicesResponse.services)
    if (liveRows.length > 0) {
      return liveRows
    }
  } catch {
    // Fall back to /projects projection until all environments expose the live services API.
  }

  try {
    const response = await getProjects()
    const fromApi = adaptProjectsToServices(response.projects)
    if (fromApi.length > 0) {
      return fromApi
    }
    throw new Error('Services registry API returned no services.')
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
