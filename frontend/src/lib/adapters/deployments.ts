import { getServiceDeployments, type ServiceDeployment } from '@/lib/api'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'

export interface DeploymentMetricSnapshot {
  before?: number
  after?: number
  delta?: number
}

export interface DeploymentHistoryItem {
  id: string
  identity: ServiceIdentity
  version: string
  outcome: string
  deployedAt?: string
  errorRatePct: DeploymentMetricSnapshot
  p95LatencyMs: DeploymentMetricSnapshot
  availabilityPct: DeploymentMetricSnapshot
  hasComparisonWindow: boolean
  regressionScore: number
}

interface DeploymentHistoryOptions {
  limit?: number
  preferBackend?: boolean
  fallbackToMock?: boolean
}

function resolveIdentity(input: ServiceIdentity | string) {
  if (typeof input === 'string') {
    return createServiceIdentity({ serviceId: input })
  }
  return createServiceIdentity(input)
}

function normalizeDeployment(item: ServiceDeployment, identity: ServiceIdentity): DeploymentHistoryItem {
  const input = item as ServiceDeployment & Record<string, unknown>
  const errorRate = getSnapshot(input, ['errorRatePct', 'errorRate'])
  const latency = getSnapshot(input, ['p95LatencyMs', 'latencyP95Ms', 'latencyMs'])
  const availability = getSnapshot(input, ['availabilityPct', 'availability'])

  const hasComparisonWindow = hasSnapshotValues(errorRate) || hasSnapshotValues(latency) || hasSnapshotValues(availability)

  return {
    id: item.id,
    identity,
    version: item.version ?? 'N/A',
    outcome: item.status ?? 'unknown',
    deployedAt: item.deployedAt,
    errorRatePct: errorRate,
    p95LatencyMs: latency,
    availabilityPct: availability,
    hasComparisonWindow,
    regressionScore: computeRegressionScore(errorRate, latency, availability),
  }
}

function sortByNewest(items: DeploymentHistoryItem[]) {
  return [...items].sort((a, b) => {
    const left = a.deployedAt ? new Date(a.deployedAt).getTime() : 0
    const right = b.deployedAt ? new Date(b.deployedAt).getTime() : 0
    return right - left
  })
}

function createMockDeployments(identity: ServiceIdentity, limit: number): DeploymentHistoryItem[] {
  const outcomes = ['succeeded', 'succeeded', 'degraded', 'failed', 'succeeded', 'unknown']
  const now = Date.now()
  const count = Math.max(limit, 10)

  return Array.from({ length: count }, (_, index) => {
    const missingComparison = index % 5 === 4
    const baselineError = 0.4 + index * 0.05
    const baselineLatency = 180 + index * 12
    const baselineAvailability = 99.95 - index * 0.02
    const regressionFactor = index % 3 === 0 ? 1.4 : index % 3 === 1 ? 0.9 : 1.1

    const errorRatePct: DeploymentMetricSnapshot = missingComparison
      ? {}
      : {
          before: Number(baselineError.toFixed(2)),
          after: Number((baselineError * regressionFactor).toFixed(2)),
        }
    const p95LatencyMs: DeploymentMetricSnapshot = missingComparison
      ? {}
      : {
          before: Number(baselineLatency.toFixed(0)),
          after: Number((baselineLatency * regressionFactor).toFixed(0)),
        }
    const availabilityPct: DeploymentMetricSnapshot = missingComparison
      ? {}
      : {
          before: Number(baselineAvailability.toFixed(2)),
          after: Number((baselineAvailability - (regressionFactor - 1) * 0.25).toFixed(2)),
        }

    attachDelta(errorRatePct)
    attachDelta(p95LatencyMs)
    attachDelta(availabilityPct)

    const hasComparisonWindow =
      hasSnapshotValues(errorRatePct) || hasSnapshotValues(p95LatencyMs) || hasSnapshotValues(availabilityPct)

    return {
      id: `${identity.serviceId}-mock-deploy-${index + 1}`,
      identity,
      version: `v0.1.${count - index}`,
      outcome: outcomes[index % outcomes.length],
      deployedAt: new Date(now - index * 1000 * 60 * 60 * 8).toISOString(),
      errorRatePct,
      p95LatencyMs,
      availabilityPct,
      hasComparisonWindow,
      regressionScore: computeRegressionScore(errorRatePct, p95LatencyMs, availabilityPct),
    }
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

// TODO: Replace mock fallback with a dedicated backend adapter once deployment-history API is finalized.
export async function getDeploymentHistory(
  service: ServiceIdentity | string,
  options: DeploymentHistoryOptions = {},
): Promise<DeploymentHistoryItem[]> {
  const identity = resolveIdentity(service)
  const serviceId = identity.serviceId
  const limit = options.limit ?? 10
  const preferBackend = options.preferBackend ?? true
  const fallbackToMock = options.fallbackToMock ?? true

  if (!serviceId.trim()) {
    throw new Error('Service ID is required to load deployment history.')
  }

  if (preferBackend) {
    try {
      const response = await getServiceDeployments(serviceId)
      const normalized = sortByNewest(response.deployments.map((item) => normalizeDeployment(item, identity))).slice(0, limit)

      if (normalized.length > 0 || !fallbackToMock) {
        return normalized
      }
    } catch (error) {
      if (!fallbackToMock) {
        throw error
      }
    }
  }

  return createMockDeployments(identity, limit).slice(0, limit)
}
