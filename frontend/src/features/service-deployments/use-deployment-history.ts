import { useCallback, useEffect, useMemo, useState } from 'react'
import { getDeploymentHistory, type DeploymentHistoryItem } from '@/lib/adapters/deployments'
import { evaluateDeploymentHistoryItem } from '@/lib/deployment-alerts'
import { createServiceIdentity } from '@/lib/service-identity'
import { normalizeServiceId, type ImpactFilterMode, type SortMode } from './shared'

export function useDeploymentHistory(serviceId: string) {
  const normalizedServiceId = useMemo(() => normalizeServiceId(serviceId), [serviceId])
  const serviceIdentity = useMemo(
    () => createServiceIdentity({ serviceId: normalizedServiceId }),
    [normalizedServiceId],
  )
  const [deployments, setDeployments] = useState<DeploymentHistoryItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [impactFilterMode, setImpactFilterMode] = useState<ImpactFilterMode>('all')
  const [actionFilter, setActionFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortMode, setSortMode] = useState<SortMode>('newest')
  const [selectedDeploymentId, setSelectedDeploymentId] = useState<string | null>(null)

  const loadDeployments = useCallback(async () => {
    setIsLoading(true)
    setError('')
    try {
      const history = await getDeploymentHistory(serviceIdentity, { limit: 20 })
      setDeployments(history)
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load deployment history'
      setError(message)
      setDeployments([])
    } finally {
      setIsLoading(false)
    }
  }, [serviceIdentity])

  useEffect(() => {
    void loadDeployments()
  }, [loadDeployments])

  const availableActions = useMemo(() => {
    return [...new Set(deployments.map((item) => item.action).filter(Boolean))].sort((left, right) =>
      left.localeCompare(right),
    )
  }, [deployments])

  const availableStatuses = useMemo(() => {
    return [...new Set(deployments.map((item) => item.outcome).filter(Boolean))].sort((left, right) =>
      left.localeCompare(right),
    )
  }, [deployments])

  const visibleDeployments = useMemo(() => {
    const filtered = deployments.filter((item) => {
      const alert = evaluateDeploymentHistoryItem(item)

      if (actionFilter !== 'all' && item.action !== actionFilter) {
        return false
      }
      if (statusFilter !== 'all' && item.outcome !== statusFilter) {
        return false
      }
      if (impactFilterMode === 'regressions') {
        return alert.suspicious
      }
      if (impactFilterMode === 'missing') {
        return !item.hasComparisonWindow && !alert.suspicious
      }
      return true
    })

    return [...filtered].sort((a, b) => {
      if (sortMode === 'worst_impact') {
        const leftAlert = evaluateDeploymentHistoryItem(a)
        const rightAlert = evaluateDeploymentHistoryItem(b)

        if (rightAlert.priority !== leftAlert.priority) {
          return rightAlert.priority - leftAlert.priority
        }

        if (b.regressionScore !== a.regressionScore) {
          return b.regressionScore - a.regressionScore
        }
      }
      const left = a.deployedAt ? new Date(a.deployedAt).getTime() : 0
      const right = b.deployedAt ? new Date(b.deployedAt).getTime() : 0
      return right - left
    })
  }, [actionFilter, deployments, impactFilterMode, sortMode, statusFilter])

  const hasAnyComparisonWindow = useMemo(
    () => deployments.some((item) => item.hasComparisonWindow),
    [deployments],
  )

  const selectedDeployment = useMemo(() => {
    if (!selectedDeploymentId || visibleDeployments.length === 0) {
      return visibleDeployments[0] ?? null
    }
    return visibleDeployments.find((item) => item.id === selectedDeploymentId) ?? visibleDeployments[0] ?? null
  }, [selectedDeploymentId, visibleDeployments])

  useEffect(() => {
    if (visibleDeployments.length === 0) {
      if (selectedDeploymentId !== null) {
        setSelectedDeploymentId(null)
      }
      return
    }

    if (!selectedDeploymentId) {
      setSelectedDeploymentId(visibleDeployments[0].id)
      return
    }

    if (!visibleDeployments.some((item) => item.id === selectedDeploymentId)) {
      setSelectedDeploymentId(visibleDeployments[0].id)
    }
  }, [selectedDeploymentId, visibleDeployments])

  return {
    normalizedServiceId,
    serviceIdentity,
    deployments,
    isLoading,
    error,
    impactFilterMode,
    setImpactFilterMode,
    actionFilter,
    setActionFilter,
    statusFilter,
    setStatusFilter,
    sortMode,
    setSortMode,
    selectedDeploymentId,
    setSelectedDeploymentId,
    availableActions,
    availableStatuses,
    visibleDeployments,
    hasAnyComparisonWindow,
    selectedDeployment,
    loadDeployments,
  }
}
