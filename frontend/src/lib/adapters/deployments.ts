import {
  getReleaseTraceability,
  getServiceDeployments,
  isApiRequestError,
  type ReleaseTraceabilityRow,
  type ServiceDeployment,
} from '@/lib/api'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'

export interface DeploymentMetricSnapshot {
  before?: number
  after?: number
  delta?: number
}

export type DeploymentEvidenceSource = 'deployment_record' | 'release_traceability'

export interface DeploymentHistoryItem {
  id: string
  identity: ServiceIdentity
  action: string
  version: string
  outcome: string
  requestedAt?: string
  requestedBy?: string
  deployedAt?: string
  gitPrUrl?: string
  gitPrNumber?: number
  compareUrl?: string
  gitRef?: string
  deployReason?: string
  failureReason?: string
  argoApp?: string
  syncStatus?: string
  healthStatus?: string
  metadata?: Record<string, unknown>
  errorRatePct: DeploymentMetricSnapshot
  p95LatencyMs: DeploymentMetricSnapshot
  availabilityPct: DeploymentMetricSnapshot
  hasComparisonWindow: boolean
  regressionScore: number
  /** Evidence source: deployment_record = tracked in DB; release_traceability = inferred from Argo/releases */
  evidenceSource: DeploymentEvidenceSource
  /** Metrics source: live_query = Prometheus live; stored_snapshot = persisted in DB; none = unavailable */
  metricsSource: 'live_query' | 'stored_snapshot' | 'none'
}

interface DeploymentHistoryOptions {
  limit?: number
}

// Deployment history can come from the dedicated deployment-records API or, on
// older backends, be inferred from release traceability rows.
const deploymentEndpointFallbackStatuses = new Set([404, 405, 501])

function resolveIdentity(input: ServiceIdentity | string) {
  if (typeof input === 'string') {
    return createServiceIdentity({ serviceId: input })
  }
  return createServiceIdentity(input)
}

function normalizeDeployment(item: ServiceDeployment, identity: ServiceIdentity): DeploymentHistoryItem {
  // Backends have evolved through a few response shapes, so normalize both the
  // nested snapshot form and older before/after/delta fields into one contract.
  const input = item as ServiceDeployment & Record<string, unknown>
  const errorRate = getSnapshot(input, ['errorRatePct', 'errorRate'])
  const latency = getSnapshot(input, ['p95LatencyMs', 'latencyP95Ms', 'latencyMs'])
  const availability = getSnapshot(input, ['availabilityPct', 'availability'])

  const hasComparisonWindow = hasSnapshotValues(errorRate) || hasSnapshotValues(latency) || hasSnapshotValues(availability)
  const deploymentIdentity = createServiceIdentity({
    serviceId: item.serviceId ?? identity.serviceId,
    serviceName: identity.serviceName,
    namespace: identity.namespace,
    env: item.env ?? identity.env,
    appLabel: identity.appLabel,
    argoAppName: item.argoApp ?? identity.argoAppName,
  })

  return {
    id: item.id,
    identity: deploymentIdentity,
    action: item.action ?? 'deploy',
    version: item.version ?? 'N/A',
    outcome: item.status ?? 'unknown',
    requestedAt: item.requestedAt,
    requestedBy: item.requestedBy,
    deployedAt: item.deployedAt ?? item.finishedAt ?? item.startedAt ?? item.requestedAt,
    gitPrUrl: item.gitPrUrl,
    gitPrNumber: item.gitPrNumber,
    compareUrl: item.compareUrl,
    gitRef: item.gitRef,
    deployReason: item.deployReason,
    failureReason: item.failureReason,
    argoApp: item.argoApp,
    syncStatus: item.syncStatus,
    healthStatus: item.healthStatus,
    metadata: item.metadata,
    errorRatePct: errorRate,
    p95LatencyMs: latency,
    availabilityPct: availability,
    hasComparisonWindow,
    regressionScore: computeRegressionScore(errorRate, latency, availability),
    evidenceSource: 'deployment_record',
    metricsSource: (item.metricsSource as DeploymentHistoryItem['metricsSource']) ?? 'none',
  }
}

function deriveVersionFromImageRef(imageRef?: string | null) {
  if (!imageRef) {
    return 'N/A'
  }

  const trimmed = imageRef.trim()
  if (!trimmed) {
    return 'N/A'
  }

  const parts = trimmed.split(':')
  return parts.length > 1 ? parts.slice(1).join(':') : trimmed
}

function normalizeReleaseDeployment(
  item: ReleaseTraceabilityRow,
  identity: ServiceIdentity,
  index: number,
): DeploymentHistoryItem {
  const deployedAt = typeof item.deployedAt === 'string' ? item.deployedAt : undefined
  const commitSha = typeof item.commitSha === 'string' ? item.commitSha : ''
  const healthStatus = typeof item.argo?.healthStatus === 'string' ? item.argo.healthStatus : undefined
  const syncStatus = typeof item.argo?.syncStatus === 'string' ? item.argo.syncStatus : undefined

  return {
    id: deployedAt || commitSha || `${identity.serviceId}:${index}`,
    identity,
    action: 'deploy',
    version: deriveVersionFromImageRef(item.imageRef),
    outcome: healthStatus || syncStatus || 'unknown',
    deployedAt,
    errorRatePct: {},
    p95LatencyMs: {},
    availabilityPct: {},
    hasComparisonWindow: false,
    regressionScore: 0,
    evidenceSource: 'release_traceability',
    metricsSource: 'none',
  }
}

function sortByNewest(items: DeploymentHistoryItem[]) {
  return [...items].sort((a, b) => {
    const left = a.deployedAt ? new Date(a.deployedAt).getTime() : 0
    const right = b.deployedAt ? new Date(b.deployedAt).getTime() : 0
    return right - left
  })
}

function toFiniteNumber(value: unknown): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return undefined
  }
  return value
}

function getSnapshot(input: Record<string, unknown>, keys: string[]): DeploymentMetricSnapshot {
  for (const key of keys) {
    const base = input[key]
    if (typeof base === 'object' && base !== null) {
      const before = toFiniteNumber((base as Record<string, unknown>).before)
      const after = toFiniteNumber((base as Record<string, unknown>).after)
      const delta = toFiniteNumber((base as Record<string, unknown>).delta)
      if (before !== undefined || after !== undefined || delta !== undefined) {
        const snapshot: DeploymentMetricSnapshot = { before, after, delta }
        attachDelta(snapshot)
        return snapshot
      }
    }

    const before = toFiniteNumber(input[`${key}Before`])
    const after = toFiniteNumber(input[`${key}After`])
    const delta = toFiniteNumber(input[`${key}Delta`])
    if (before !== undefined || after !== undefined || delta !== undefined) {
      const snapshot: DeploymentMetricSnapshot = { before, after, delta }
      attachDelta(snapshot)
      return snapshot
    }
  }

  return {}
}

function attachDelta(snapshot: DeploymentMetricSnapshot) {
  if (snapshot.delta !== undefined) {
    return
  }

  if (snapshot.before === undefined || snapshot.after === undefined) {
    return
  }

  snapshot.delta = Number((snapshot.after - snapshot.before).toFixed(3))
}

function hasSnapshotValues(snapshot: DeploymentMetricSnapshot) {
  return (
    snapshot.before !== undefined ||
    snapshot.after !== undefined ||
    snapshot.delta !== undefined
  )
}

function computeRegressionScore(
  errorRate: DeploymentMetricSnapshot,
  latency: DeploymentMetricSnapshot,
  availability: DeploymentMetricSnapshot,
) {
  const errorPenalty = Math.max(0, errorRate.delta ?? 0) * 10
  const latencyPenalty = Math.max(0, latency.delta ?? 0) / 40
  const availabilityPenalty = Math.max(0, -(availability.delta ?? 0)) * 8
  return Number((errorPenalty + latencyPenalty + availabilityPenalty).toFixed(3))
}

async function getReleaseDeploymentFallback(identity: ServiceIdentity, limit: number) {
  // Release rows are less precise than deployment records, but they preserve a
  // useful timeline when the richer endpoint has not been enabled yet.
  const rows = await getReleaseTraceability({
    env: identity.env,
    serviceId: identity.serviceId,
    limit,
  })

  return sortByNewest(
    rows.map((item, index) => normalizeReleaseDeployment(item, identity, index)).filter((item) => {
      return item.deployedAt || item.version !== 'N/A' || item.outcome !== 'unknown'
    }),
  ).slice(0, limit)
}

export async function getDeploymentHistory(
  service: ServiceIdentity | string,
  options: DeploymentHistoryOptions = {},
): Promise<DeploymentHistoryItem[]> {
  const identity = resolveIdentity(service)
  const serviceId = identity.serviceId
  const limit = options.limit ?? 10

  if (!serviceId.trim()) {
    throw new Error('Service ID is required to load deployment history.')
  }

  try {
    const response = await getServiceDeployments(serviceId)
    const normalized = sortByNewest(response.deployments.map((item) => normalizeDeployment(item, identity))).slice(0, limit)
    if (normalized.length > 0) {
      return normalized
    }
    return await getReleaseDeploymentFallback(identity, limit)
  } catch (error) {
    // Missing endpoint statuses mean "feature not supported here", not that the
    // service failed to load, so switch to the traceability-based fallback.
    if (isApiRequestError(error) && deploymentEndpointFallbackStatuses.has(error.status)) {
      return await getReleaseDeploymentFallback(identity, limit)
    }
    throw error
  }
}
