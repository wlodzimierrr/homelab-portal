import { ApiRequestError, getProjects, isApiRequestError, request, type Project } from '@/lib/api'
import { buildArgoAppUrl } from '@/lib/config'
import { config } from '@/lib/config'

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

interface ReleaseDashboardApiEntry {
  id?: string
  serviceId?: string
  serviceName?: string
  environment?: string
  commitSha?: string
  commitUrl?: string
  desiredCommitSha?: string
  image?: string
  imageTag?: string
  imageUrl?: string
  desiredImage?: string
  argoApp?: string
  argoAppUrl?: string
  sync?: string
  health?: string
  drift?: boolean
  deployedAt?: string
}

interface ReleaseDashboardResponse {
  releases?: ReleaseDashboardApiEntry[]
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

interface ReleaseDashboardSamplePayload {
  releases?: ReleaseDashboardApiEntry[]
}

const releaseDashboardSampleUrl = new URL('../../../release-dashboard.sample.json', import.meta.url).toString()
const releaseDashboardMissingStatuses = new Set([404, 405, 501])
const enableSampleFallback = import.meta.env.DEV || config.enableReleaseSampleFallback

type ReleaseDashboardApiAvailability = 'unknown' | 'available' | 'unavailable'
let releaseDashboardApiAvailability: ReleaseDashboardApiAvailability = 'unknown'

export type ReleaseDashboardSource = 'live_api' | 'projects_fallback' | 'sample_fallback'

export interface ReleaseDashboardResult {
  rows: ReleaseDashboardEntry[]
  dataSource: ReleaseDashboardSource
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

function toEntry(input: ReleaseDashboardApiEntry): ReleaseDashboardEntry | null {
  const serviceId = typeof input.serviceId === 'string' && input.serviceId.trim() ? input.serviceId.trim() : ''
  const serviceName =
    typeof input.serviceName === 'string' && input.serviceName.trim() ? input.serviceName.trim() : serviceId
  const environment =
    typeof input.environment === 'string' && input.environment.trim() ? input.environment.trim() : ''

  if (!serviceId || !serviceName || !environment) {
    return null
  }

  const image = typeof input.image === 'string' ? input.image : undefined
  const desiredImage = typeof input.desiredImage === 'string' ? input.desiredImage : undefined
  const commitSha = typeof input.commitSha === 'string' ? input.commitSha : undefined
  const desiredCommitSha = typeof input.desiredCommitSha === 'string' ? input.desiredCommitSha : undefined

  const driftSignals = [
    normalizeSyncStatus(input.sync) === 'out_of_sync',
    Boolean(desiredImage && image && desiredImage !== image),
    Boolean(desiredCommitSha && commitSha && desiredCommitSha !== commitSha),
    input.drift === true,
  ]

  return {
    id:
      typeof input.id === 'string' && input.id.trim()
        ? input.id.trim()
        : `${serviceId}:${environment}`,
    serviceId,
    serviceName,
    environment,
    commitSha,
    commitUrl: typeof input.commitUrl === 'string' ? input.commitUrl : undefined,
    desiredCommitSha,
    image,
    imageTag: typeof input.imageTag === 'string' ? input.imageTag : undefined,
    imageUrl: typeof input.imageUrl === 'string' ? input.imageUrl : undefined,
    desiredImage,
    argoApp: typeof input.argoApp === 'string' ? input.argoApp : undefined,
    argoAppUrl: typeof input.argoAppUrl === 'string' ? input.argoAppUrl : undefined,
    sync: normalizeSyncStatus(input.sync),
    health: normalizeHealthStatus(input.health),
    drift: driftSignals.some(Boolean),
    deployedAt: typeof input.deployedAt === 'string' ? input.deployedAt : undefined,
  }
}

function adaptReleasePayload(payload: ReleaseDashboardResponse | ReleaseDashboardSamplePayload): ReleaseDashboardEntry[] {
  if (!Array.isArray(payload.releases)) {
    return []
  }

  return payload.releases
    .map((entry) => toEntry(entry))
    .filter((entry): entry is ReleaseDashboardEntry => entry !== null)
    .sort((a, b) => {
      if (a.serviceName === b.serviceName) {
        return a.environment.localeCompare(b.environment)
      }
      return a.serviceName.localeCompare(b.serviceName)
    })
}

function adaptLiveReleaseRows(rows: ReleaseTraceabilityApiRow[]): ReleaseDashboardEntry[] {
  return rows
    .map((row): ReleaseDashboardEntry | null => {
      const serviceId =
        typeof row.serviceId === 'string' && row.serviceId.trim() ? row.serviceId.trim() : ''
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
        serviceName: serviceId,
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

      return {
        id: project.id,
        serviceId: project.name,
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

async function loadSampleReleaseDashboard() {
  const response = await fetch(releaseDashboardSampleUrl)
  if (!response.ok) {
    throw new Error('Failed to load fallback release dashboard data.')
  }

  const payload = (await response.json()) as ReleaseDashboardSamplePayload
  return adaptReleasePayload(payload)
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

  try {
    const fromApi = await getReleaseDashboardFromApi()
    if (fromApi.length > 0) {
      return {
        rows: fromApi,
        dataSource: 'live_api',
      }
    }
  } catch (error) {
    if (isApiRequestError(error) && releaseDashboardMissingStatuses.has(error.status)) {
      releaseDashboardApiAvailability = 'unavailable'
    }
    apiError = error instanceof Error ? error : new Error('Failed to load release dashboard data from API.')
  }

  try {
    const projects = await getProjects()
    const fromProjects = adaptProjectsToReleaseRows(projects.projects)
    if (fromProjects.length > 0) {
      return {
        rows: fromProjects,
        dataSource: 'projects_fallback',
      }
    }
  } catch (error) {
    if (!apiError) {
      apiError = error instanceof Error ? error : new Error('Failed to load release dashboard data from projects.')
    }
  }

  if (!enableSampleFallback) {
    if (apiError) {
      throw apiError
    }
    throw new Error('Release dashboard data unavailable and sample fallback disabled.')
  }

  try {
    return {
      rows: await loadSampleReleaseDashboard(),
      dataSource: 'sample_fallback',
    }
  } catch (fallbackError) {
    if (apiError) {
      throw apiError
    }

    throw fallbackError instanceof Error
      ? fallbackError
      : new Error('Failed to load fallback release dashboard data.')
  }
}
