import { ApiRequestError, getProjects, isApiRequestError, request, type Project } from '@/lib/api'
import { getServicesRegistry, type ServiceRegistryItem } from '@/lib/adapters/services'
import { buildArgoAppUrl } from '@/lib/config'
import { normalizeServiceId } from '@/lib/service-identity'

export type ReleaseSyncStatus = 'synced' | 'out_of_sync' | 'unknown'
export type ReleaseHealthStatus = 'healthy' | 'degraded' | 'unknown'

export interface ReleaseDashboardEntry {
  id: string
  serviceId: string
  serviceName: string
  environment: string
  commitSha?: string
  commitUrl?: string
  desiredCommitSha?: string
  image?: string
  imageTag?: string
  imageUrl?: string
  desiredImage?: string
  argoApp?: string
  argoAppUrl?: string
  sync: ReleaseSyncStatus
  health: ReleaseHealthStatus
  drift: boolean
  deployedAt?: string
}

interface ReleaseTraceabilityApiRow {
  serviceId?: string
  env?: string
  commitSha?: string | null
  imageRef?: string | null
  deployedAt?: string | null
  argo?: {
    appName?: string | null
    syncStatus?: string | null
    healthStatus?: string | null
    revision?: string | null
  }
  drift?: {
    isDrifted?: boolean
    expectedRevision?: string | null
    liveRevision?: string | null
  }
}

const releaseDashboardMissingStatuses = new Set([404, 405, 501])

type ReleaseDashboardApiAvailability = 'unknown' | 'available' | 'unavailable'
let releaseDashboardApiAvailability: ReleaseDashboardApiAvailability = 'unknown'

export type ReleaseDashboardSource = 'live_api' | 'projects_fallback'
export type ReleaseDashboardLiveStatus = 'live_api' | 'fallback_projects'

export interface ReleaseDashboardResult {
  rows: ReleaseDashboardEntry[]
  dataSource: ReleaseDashboardSource
  liveStatus: ReleaseDashboardLiveStatus
  unknownFieldCount: number
  warnings: string[]
}

function countUnknownTraceabilityFields(rows: ReleaseDashboardEntry[]) {
  let unknownFieldCount = 0
  for (const row of rows) {
    if (!row.commitSha) {
      unknownFieldCount += 1
    }
    if (!row.image) {
      unknownFieldCount += 1
    }
    if (!row.argoApp) {
      unknownFieldCount += 1
    }
    if (!row.deployedAt) {
      unknownFieldCount += 1
    }
  }
  return unknownFieldCount
}

function normalizeSyncStatus(value?: string): ReleaseSyncStatus {
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

function normalizeHealthStatus(value?: string): ReleaseHealthStatus {
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

function adaptLiveReleaseRows(rows: ReleaseTraceabilityApiRow[]): ReleaseDashboardEntry[] {
  return rows
    .map((row): ReleaseDashboardEntry | null => {
      const rawServiceId =
        typeof row.serviceId === 'string' && row.serviceId.trim() ? row.serviceId.trim() : ''
      const serviceId = normalizeServiceId(rawServiceId) || rawServiceId
      const environment = typeof row.env === 'string' && row.env.trim() ? row.env.trim() : ''

      if (!serviceId || !environment) {
        return null
      }

      const imageRef = typeof row.imageRef === 'string' ? row.imageRef : undefined
      const commitSha = typeof row.commitSha === 'string' ? row.commitSha : undefined
      const expectedRevision =
        typeof row.drift?.expectedRevision === 'string' ? row.drift.expectedRevision : undefined
      const liveRevision = typeof row.drift?.liveRevision === 'string' ? row.drift.liveRevision : undefined
      const drift = row.drift?.isDrifted === true

      return {
        id: `${serviceId}:${environment}`,
        serviceId,
        serviceName: rawServiceId,
        environment,
        commitSha,
        desiredCommitSha: expectedRevision,
        image: imageRef,
        imageTag: imageRef?.split(':').slice(1).join(':') || imageRef,
        desiredImage: undefined,
        argoApp: typeof row.argo?.appName === 'string' ? row.argo.appName : undefined,
        argoAppUrl: buildArgoAppUrl(serviceId),
        sync: normalizeSyncStatus(typeof row.argo?.syncStatus === 'string' ? row.argo.syncStatus : undefined),
        health: normalizeHealthStatus(typeof row.argo?.healthStatus === 'string' ? row.argo.healthStatus : undefined),
        drift: drift || Boolean(expectedRevision && liveRevision && expectedRevision !== liveRevision),
        deployedAt: typeof row.deployedAt === 'string' ? row.deployedAt : undefined,
      }
    })
    .filter((row): row is ReleaseDashboardEntry => row !== null)
    .sort((a, b) => {
      if (a.serviceName === b.serviceName) {
        return a.environment.localeCompare(b.environment)
      }
      return a.serviceName.localeCompare(b.serviceName)
    })
}

function adaptProjectsToReleaseRows(projects: Project[]): ReleaseDashboardEntry[] {
  return projects
    .map((project) => {
      const sync = normalizeSyncStatus(project.sync)
      const health = normalizeHealthStatus(project.health)
      const serviceId = normalizeServiceId(project.name) || normalizeServiceId(project.id) || project.id

      return {
        id: project.id,
        serviceId,
        serviceName: project.name,
        environment: project.environment,
        sync,
        health,
        drift: sync === 'out_of_sync',
        deployedAt: project.lastDeployAt,
      }
    })
    .sort((a, b) => {
      if (a.serviceName === b.serviceName) {
        return a.environment.localeCompare(b.environment)
      }
      return a.serviceName.localeCompare(b.serviceName)
    })
}

function normalizeLookup(value: string) {
  return normalizeServiceId(value) || value.trim().toLowerCase()
}

function joinRowsWithServices(rows: ReleaseDashboardEntry[], services: ServiceRegistryItem[]) {
  const byId = new Map<string, ServiceRegistryItem>()
  const byName = new Map<string, ServiceRegistryItem>()

  for (const service of services) {
    byId.set(normalizeLookup(service.id), service)
    byName.set(normalizeLookup(service.name), service)
  }

  const unresolvedKeys: string[] = []

  const joined = rows.map((row) => {
    const match = byId.get(normalizeLookup(row.serviceId)) ?? byName.get(normalizeLookup(row.serviceName))
    if (!match) {
      unresolvedKeys.push(`${row.serviceId}|${row.serviceName}|${row.environment}`)
      return row
    }

    return {
      ...row,
      serviceId: match.id,
      serviceName: match.name,
      argoApp: row.argoApp ?? match.argoAppName,
    }
  })

  const unresolved = joined.filter((row) => {
    const byServiceId = byId.has(normalizeLookup(row.serviceId))
    const byServiceName = byName.has(normalizeLookup(row.serviceName))
    return !byServiceId && !byServiceName
  }).length

  return {
    rows: joined,
    unresolved,
    unresolvedKeys,
  }
}

async function getReleaseDashboardFromApi() {
  if (releaseDashboardApiAvailability === 'unavailable') {
    throw new ApiRequestError('Release dashboard endpoint is not available in this backend.', 404)
  }

  const payload = await request<ReleaseTraceabilityApiRow[]>('/releases?limit=50')
  releaseDashboardApiAvailability = 'available'
  return adaptLiveReleaseRows(payload)
}

export async function getReleaseDashboardEntries(): Promise<ReleaseDashboardResult> {
  let apiError: Error | null = null
  let baseRows: ReleaseDashboardEntry[] = []
  let dataSource: ReleaseDashboardSource = 'projects_fallback'
  const warnings: string[] = []

  try {
    const fromApi = await getReleaseDashboardFromApi()
    if (fromApi.length > 0) {
      baseRows = fromApi
      dataSource = 'live_api'
    }
  } catch (error) {
    if (isApiRequestError(error) && releaseDashboardMissingStatuses.has(error.status)) {
      releaseDashboardApiAvailability = 'unavailable'
    }
    apiError = error instanceof Error ? error : new Error('Failed to load release dashboard data from API.')
  }

  try {
    if (baseRows.length === 0) {
      const projects = await getProjects()
      const fromProjects = adaptProjectsToReleaseRows(projects.projects)
      if (fromProjects.length > 0) {
        baseRows = fromProjects
        dataSource = 'projects_fallback'
      }
    }
  } catch (error) {
    if (!apiError) {
      apiError = error instanceof Error ? error : new Error('Failed to load release dashboard data from projects.')
    }
  }

  if (baseRows.length > 0) {
    try {
      const services = await getServicesRegistry()
      const joined = joinRowsWithServices(baseRows, services)
      if (joined.unresolved > 0) {
        warnings.push(
          `Service identity join incomplete: ${joined.unresolved} release row(s) are not mapped to registry metadata.`,
        )
        const keysPreview = joined.unresolvedKeys.slice(0, 5).join(', ')
        warnings.push(`Unmatched release keys: ${keysPreview}${joined.unresolvedKeys.length > 5 ? ', ...' : ''}`)
      }
      return {
        rows: joined.rows,
        dataSource,
        liveStatus: dataSource === 'live_api' ? 'live_api' : 'fallback_projects',
        unknownFieldCount: countUnknownTraceabilityFields(joined.rows),
        warnings,
      }
    } catch (joinError) {
      warnings.push(
        `Service registry API unavailable: release rows rendered without registry identity join (${joinError instanceof Error ? joinError.message : 'unknown error'}).`,
      )
      return {
        rows: baseRows,
        dataSource,
        liveStatus: dataSource === 'live_api' ? 'live_api' : 'fallback_projects',
        unknownFieldCount: countUnknownTraceabilityFields(baseRows),
        warnings,
      }
    }
  }

  if (apiError) {
    throw apiError
  }
  throw new Error('Release dashboard data unavailable from live API sources.')
}
