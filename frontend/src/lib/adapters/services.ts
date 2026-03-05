import { getProjects, type Project } from '@/lib/api'

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
}

interface ServicesSamplePayload {
  services?: Array<Partial<ServiceRegistryItem>>
}

const servicesSampleUrl = new URL('../../../services.sample.json', import.meta.url).toString()

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
    const key = project.name.trim().toLowerCase()
    const current = grouped.get(key)
    const nextHealth = normalizeHealthStatus(project.health)
    const nextSync = normalizeSyncStatus(project.sync)

    if (!current) {
      grouped.set(key, {
        id: project.name,
        name: project.name,
        environments: [project.environment],
        health: nextHealth,
        sync: nextSync,
        publicUrl: project.publicUrl,
        internalUrls: project.internalUrl ? [project.internalUrl] : undefined,
        lastDeployAt: project.lastDeployAt,
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
    }

    if (!current.lastDeployAt && project.lastDeployAt) {
      current.lastDeployAt = project.lastDeployAt
    }
  }

  return [...grouped.values()].sort((a, b) => a.name.localeCompare(b.name))
}

function adaptSampleToServices(payload: ServicesSamplePayload): ServiceRegistryItem[] {
  if (!Array.isArray(payload.services)) {
    return []
  }

  const adapted = payload.services
    .map((service): ServiceRegistryItem | null => {
      const id = typeof service.id === 'string' && service.id.trim() ? service.id.trim() : ''
      const name = typeof service.name === 'string' && service.name.trim() ? service.name.trim() : id

      if (!id || !name) {
        return null
      }

      const normalized: ServiceRegistryItem = {
        id,
        name,
        environments: Array.isArray(service.environments)
          ? service.environments.filter((item): item is string => typeof item === 'string' && item.trim() !== '')
          : [],
        health: normalizeHealthStatus(service.health),
        sync: normalizeSyncStatus(service.sync),
      }

      if (typeof service.publicUrl === 'string') {
        normalized.publicUrl = service.publicUrl
      }

      if (Array.isArray(service.internalUrls)) {
        const internalUrls = service.internalUrls.filter(
          (item): item is string => typeof item === 'string' && item.trim() !== ''
        )
        if (internalUrls.length > 0) {
          normalized.internalUrls = internalUrls
        }
      }

      if (typeof service.lastDeployAt === 'string') {
        normalized.lastDeployAt = service.lastDeployAt
      }

      if (typeof service.uptime24hPct === 'number' && Number.isFinite(service.uptime24hPct)) {
        normalized.uptime24hPct = service.uptime24hPct
      }

      if (typeof service.uptime7dPct === 'number' && Number.isFinite(service.uptime7dPct)) {
        normalized.uptime7dPct = service.uptime7dPct
      }

      if (typeof service.metricsLastRefreshedAt === 'string') {
        normalized.metricsLastRefreshedAt = service.metricsLastRefreshedAt
      }

      if (typeof service.namespace === 'string') {
        normalized.namespace = service.namespace
      }

      if (typeof service.appLabel === 'string') {
        normalized.appLabel = service.appLabel
      }

      return normalized
    })
    .filter((item): item is ServiceRegistryItem => item !== null)

  return adapted.sort((a, b) => a.name.localeCompare(b.name))
}

async function loadSampleServices() {
  const response = await fetch(servicesSampleUrl)
  if (!response.ok) {
    throw new Error('Failed to load fallback services registry.')
  }

  const payload = (await response.json()) as ServicesSamplePayload
  return adaptSampleToServices(payload)
}

export async function getServicesRegistry() {
  let apiError: Error | null = null

  try {
    const response = await getProjects()
    const fromApi = adaptProjectsToServices(response.projects)
    if (fromApi.length > 0) {
      return fromApi
    }
  } catch (error) {
    apiError = error instanceof Error ? error : new Error('Failed to load services from API.')
  }

  try {
    return await loadSampleServices()
  } catch (fallbackError) {
    if (apiError) {
      throw apiError
    }
    throw fallbackError instanceof Error
      ? fallbackError
      : new Error('Failed to load fallback services registry.')
  }
}
