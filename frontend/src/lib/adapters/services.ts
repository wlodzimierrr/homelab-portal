import { getProjects, getServices, type Project, type ServiceRegistryApiRow } from '@/lib/api/catalog'
import { getReleaseTraceability, type ReleaseTraceabilityRow } from '@/lib/api/deployments'
import { isApiRequestError } from '@/lib/http/errors'
import { getServiceMetricsSummary } from '@/lib/adapters/service-metrics'
import { createServiceIdentity, normalizeServiceId, parseNamespaceFromInternalUrl, type ServiceIdentity } from '@/lib/service-identity'

// This adapter reconciles three views of a service:
// live registry rows, GitOps/project metadata, and release/metrics observability.
export type ServiceHealth = 'healthy' | 'degraded' | 'unknown'
export type ServiceSync = 'synced' | 'out_of_sync' | 'unknown'

export interface ServiceEnvironmentStatus {
  env: string
  health: ServiceHealth
  sync: ServiceSync
  argoAppName?: string
  lastDeployAt?: string
  state: 'live' | 'gitops_only'
}

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
  owner?: string
  repoUrl?: string
  runbookUrl?: string
  observabilityMode?: 'app-native' | 'ingress-derived' | 'no-http'
  environmentStatuses: ServiceEnvironmentStatus[]
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
  // Projects are the fallback catalog source when the live service registry API is
  // unavailable. Treat them as GitOps-only services and infer missing runtime fields.
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
        owner: project.owner,
        repoUrl: project.repoUrl,
        runbookUrl: project.runbookUrl,
        environmentStatuses: [
          {
            env: project.environment,
            health: nextHealth,
            sync: nextSync,
            argoAppName: `${canonicalId}-${project.environment}`,
            lastDeployAt: project.lastDeployAt,
            state: 'gitops_only',
          },
        ],
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

    if (!current.owner && project.owner) {
      current.owner = project.owner
    }

    if (!current.repoUrl && project.repoUrl) {
      current.repoUrl = project.repoUrl
    }

    if (!current.runbookUrl && project.runbookUrl) {
      current.runbookUrl = project.runbookUrl
    }

    const existingEnvironment = current.environmentStatuses.find((status) => status.env === project.environment)
    if (!existingEnvironment) {
      current.environmentStatuses.push({
        env: project.environment,
        health: nextHealth,
        sync: nextSync,
        argoAppName: `${canonicalId}-${project.environment}`,
        lastDeployAt: project.lastDeployAt,
        state: 'gitops_only',
      })
    }
  }

  for (const service of grouped.values()) {
    service.environmentStatuses.sort((a, b) => a.env.localeCompare(b.env))
  }

  return [...grouped.values()].sort((a, b) => a.name.localeCompare(b.name))
}

function adaptApiServices(rows: ServiceRegistryApiRow[]): ServiceRegistryItem[] {
  // Live registry rows are environment-scoped, so group them back into a single
  // service record that the UI can render across multiple environments.
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
        observabilityMode: row.observabilityMode,
        environmentStatuses: [
          {
            env: row.env,
            health: 'unknown',
            sync: 'unknown',
            argoAppName: row.argoAppName,
            state: 'live',
          },
        ],
      })
      continue
    }

    if (!current.environments.includes(row.env)) {
      current.environments.push(row.env)
      current.environments.sort((a, b) => a.localeCompare(b))
    }

    const existingEnvironment = current.environmentStatuses.find((status) => status.env === row.env)
    if (!existingEnvironment) {
      current.environmentStatuses.push({
        env: row.env,
        health: 'unknown',
        sync: 'unknown',
        argoAppName: row.argoAppName,
        state: 'live',
      })
    } else if (!existingEnvironment.argoAppName && row.argoAppName) {
      existingEnvironment.argoAppName = row.argoAppName
    }
  }

  for (const service of grouped.values()) {
    service.environmentStatuses.sort((a, b) => a.env.localeCompare(b.env))
  }

  return [...grouped.values()].sort((a, b) => a.name.localeCompare(b.name))
}

function mergeProjectMetadata(services: ServiceRegistryItem[], projects: Project[]): ServiceRegistryItem[] {
  // Prefer live-discovered services as the base shape, then enrich them with
  // GitOps metadata such as ownership, repo links, runbooks, and public URLs.
  const byId = new Map<string, ServiceRegistryItem>()
  for (const service of services) {
      byId.set(service.id, {
        ...service,
        environments: [...service.environments],
        internalUrls: service.internalUrls ? [...service.internalUrls] : undefined,
        environmentStatuses: service.environmentStatuses.map((status) => ({ ...status })),
      })
    }

  for (const project of projects) {
    const canonicalId =
      normalizeServiceId(project.name) ||
      normalizeServiceId(project.id) ||
      project.id.trim().toLowerCase()
    const nextHealth = normalizeHealthStatus(project.health)
    const nextSync = normalizeSyncStatus(project.sync)
    const service = byId.get(canonicalId)
    if (!service) {
      byId.set(canonicalId, {
        id: canonicalId,
        name: project.name,
        environments: [project.environment],
        health: nextHealth,
        sync: nextSync,
        publicUrl: project.publicUrl,
        internalUrls: project.internalUrl ? [project.internalUrl] : undefined,
        lastDeployAt: project.lastDeployAt,
        namespace: parseNamespaceFromInternalUrl(project.internalUrl) ?? undefined,
        appLabel: canonicalId,
        argoAppName: `${canonicalId}-${project.environment}`,
        owner: project.owner,
        repoUrl: project.repoUrl,
        runbookUrl: project.runbookUrl,
        environmentStatuses: [
          {
            env: project.environment,
            health: nextHealth,
            sync: nextSync,
            argoAppName: `${canonicalId}-${project.environment}`,
            lastDeployAt: project.lastDeployAt,
            state: 'gitops_only',
          },
        ],
      })
      continue
    }

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

    if (!service.owner && project.owner) {
      service.owner = project.owner
    }

    if (!service.repoUrl && project.repoUrl) {
      service.repoUrl = project.repoUrl
    }

    if (!service.runbookUrl && project.runbookUrl) {
      service.runbookUrl = project.runbookUrl
    }

    const existingEnvironment = service.environmentStatuses.find((status) => status.env === project.environment)
    if (!existingEnvironment) {
      service.environmentStatuses.push({
        env: project.environment,
        health: nextHealth,
        sync: nextSync,
        argoAppName: `${canonicalId}-${project.environment}`,
        lastDeployAt: project.lastDeployAt,
        state: 'gitops_only',
      })
    }
  }

  for (const service of byId.values()) {
    service.environmentStatuses.sort((a, b) => a.env.localeCompare(b.env))
  }

  return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name))
}

function cloneService(service: ServiceRegistryItem): ServiceRegistryItem {
  return {
    ...service,
    environments: [...service.environments],
    internalUrls: service.internalUrls ? [...service.internalUrls] : undefined,
    environmentStatuses: service.environmentStatuses.map((status) => ({ ...status })),
  }
}

async function enrichServicesWithLiveMetadata(services: ServiceRegistryItem[]) {
  // Release traceability and metrics are intentionally best-effort. A failure in
  // either system should not prevent the service catalog itself from rendering.
  const releaseRows = await getReleaseTraceability({ limit: 200 }).catch(() => [])
  const releaseByKey = new Map<string, ReleaseTraceabilityRow>()
  for (const row of releaseRows) {
    const serviceId = typeof row.serviceId === 'string' ? normalizeServiceId(row.serviceId) || row.serviceId : ''
    const env = typeof row.env === 'string' ? row.env : ''
    if (!serviceId || !env) {
      continue
    }
    const key = `${serviceId}:${env}`
    const current = releaseByKey.get(key)
    const currentDeployedAt = current?.deployedAt ? new Date(current.deployedAt).getTime() : 0
    const nextDeployedAt = row.deployedAt ? new Date(row.deployedAt).getTime() : 0
    if (!current || nextDeployedAt >= currentDeployedAt) {
      releaseByKey.set(key, row)
    }
  }

  const enriched = await Promise.all(
    services.map(async (service) => {
      const next = cloneService(service)
      const [metrics24Result, metrics7Result] = await Promise.allSettled([
        getServiceMetricsSummary(service.id, '24h'),
        getServiceMetricsSummary(service.id, '7d'),
      ])

      for (const env of service.environments) {
        const release = releaseByKey.get(`${service.id}:${env}`)
        if (!release) {
          continue
        }
        const nextHealth = normalizeHealthStatus(
          typeof release.argo?.healthStatus === 'string' ? release.argo.healthStatus : undefined,
        )
        const nextSync = normalizeSyncStatus(
          typeof release.argo?.syncStatus === 'string' ? release.argo.syncStatus : undefined,
        )
        if (next.health === 'unknown' && nextHealth !== 'unknown') {
          next.health = nextHealth
        }
        if (next.sync === 'unknown' && nextSync !== 'unknown') {
          next.sync = nextSync
        }
        if (!next.lastDeployAt && typeof release.deployedAt === 'string') {
          next.lastDeployAt = release.deployedAt
        }
        if (!next.argoAppName && typeof release.argo?.appName === 'string') {
          next.argoAppName = release.argo.appName
        }

        const environmentStatus = next.environmentStatuses.find((status) => status.env === env)
        if (environmentStatus) {
          environmentStatus.health = nextHealth
          environmentStatus.sync = nextSync
          environmentStatus.lastDeployAt =
            typeof release.deployedAt === 'string' ? release.deployedAt : environmentStatus.lastDeployAt
          environmentStatus.argoAppName =
            typeof release.argo?.appName === 'string' ? release.argo.appName : environmentStatus.argoAppName
          environmentStatus.state = 'live'
        } else {
          next.environmentStatuses.push({
            env,
            health: nextHealth,
            sync: nextSync,
            argoAppName: typeof release.argo?.appName === 'string' ? release.argo.appName : undefined,
            lastDeployAt: typeof release.deployedAt === 'string' ? release.deployedAt : undefined,
            state: 'live',
          })
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

      next.environmentStatuses.sort((a, b) => a.env.localeCompare(b.env))
      return next
    }),
  )

  return enriched.sort((a, b) => a.name.localeCompare(b.name))
}

export async function getServicesRegistry() {
  // Prefer the service-centric API, then fall back to project-backed metadata for
  // older backends. This keeps the UI compatible during backend rollout windows.
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
  // Resolve against the merged registry first so downstream pages inherit the same
  // canonical namespace/app/env assumptions as the services catalog.
  const services = await getServicesRegistry()
  const match = services.find((service) => service.id.trim().toLowerCase() === serviceId.trim().toLowerCase())
  if (!match) {
    return createServiceIdentity({ serviceId, env })
  }
  return deriveServiceIdentity(match, env)
}
