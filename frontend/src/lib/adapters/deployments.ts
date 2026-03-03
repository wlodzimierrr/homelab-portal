import { getServiceDeployments, type ServiceDeployment } from '@/lib/api'

export interface DeploymentHistoryItem {
  id: string
  version: string
  outcome: string
  deployedAt?: string
}

interface DeploymentHistoryOptions {
  limit?: number
  preferBackend?: boolean
  fallbackToMock?: boolean
}

function normalizeDeployment(item: ServiceDeployment): DeploymentHistoryItem {
  return {
    id: item.id,
    version: item.version ?? 'N/A',
    outcome: item.status ?? 'unknown',
    deployedAt: item.deployedAt,
  }
}

function sortByNewest(items: DeploymentHistoryItem[]) {
  return [...items].sort((a, b) => {
    const left = a.deployedAt ? new Date(a.deployedAt).getTime() : 0
    const right = b.deployedAt ? new Date(b.deployedAt).getTime() : 0
    return right - left
  })
}

function createMockDeployments(serviceId: string, limit: number): DeploymentHistoryItem[] {
  const outcomes = ['succeeded', 'succeeded', 'degraded', 'failed', 'succeeded', 'unknown']
  const now = Date.now()
  const count = Math.max(limit, 10)

  return Array.from({ length: count }, (_, index) => ({
    id: `${serviceId}-mock-deploy-${index + 1}`,
    version: `v0.1.${count - index}`,
    outcome: outcomes[index % outcomes.length],
    deployedAt: new Date(now - index * 1000 * 60 * 60 * 8).toISOString(),
  }))
}

// TODO: Replace mock fallback with a dedicated backend adapter once deployment-history API is finalized.
export async function getDeploymentHistory(
  serviceId: string,
  options: DeploymentHistoryOptions = {},
): Promise<DeploymentHistoryItem[]> {
  const limit = options.limit ?? 10
  const preferBackend = options.preferBackend ?? true
  const fallbackToMock = options.fallbackToMock ?? true

  if (!serviceId.trim()) {
    throw new Error('Service ID is required to load deployment history.')
  }

  if (preferBackend) {
    try {
      const response = await getServiceDeployments(serviceId)
      const normalized = sortByNewest(response.deployments.map(normalizeDeployment)).slice(0, limit)

      if (normalized.length > 0 || !fallbackToMock) {
        return normalized
      }
    } catch (error) {
      if (!fallbackToMock) {
        throw error
      }
    }
  }

  return createMockDeployments(serviceId, limit).slice(0, limit)
}
