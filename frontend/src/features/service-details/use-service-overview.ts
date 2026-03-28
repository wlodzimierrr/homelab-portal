import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getCatalogReconciliation,
  type CatalogJoinRow,
  getProjects,
  getService,
  type ServiceCapabilities,
  type ServiceProjectContext,
} from '@/lib/api/catalog'
import { getReleaseTraceability, getServiceDeploymentInfo } from '@/lib/api/deployments'
import { getDeploymentHistory, type DeploymentHistoryItem } from '@/lib/adapters/deployments'
import { getServiceIdentity } from '@/lib/adapters/services'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'
import type { ServiceDeploymentInfo } from '@/lib/api/deployments'
import {
  buildFromProjects,
  buildIdentityFromServiceDetails,
  buildOverviewFromReleaseRows,
  buildEndpointList,
  normalizeHealthStatus,
  normalizeSyncStatus,
  safeDecodeServiceId,
  type ServiceOverviewData,
} from './shared'

export function useServiceOverview(serviceId: string) {
  const decodedServiceId = useMemo(() => safeDecodeServiceId(serviceId), [serviceId])
  const [serviceIdentity, setServiceIdentity] = useState<ServiceIdentity>(() =>
    createServiceIdentity({ serviceId: decodedServiceId }),
  )
  const [overview, setOverview] = useState<ServiceOverviewData | null>(null)
  const [deploymentHistory, setDeploymentHistory] = useState<DeploymentHistoryItem[]>([])
  const [deploymentInfo, setDeploymentInfo] = useState<ServiceDeploymentInfo | null>(null)
  const [deploymentInfoLoading, setDeploymentInfoLoading] = useState(true)
  const [deploymentInfoError, setDeploymentInfoError] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [deploymentHistoryUnavailable, setDeploymentHistoryUnavailable] = useState(false)
  const [deploymentHistoryError, setDeploymentHistoryError] = useState('')
  const [projectContext, setProjectContext] = useState<ServiceProjectContext | null>(null)
  const [capabilities, setCapabilities] = useState<ServiceCapabilities | null>(null)

  const loadOverview = useCallback(async (options?: { background?: boolean }) => {
    const background = options?.background === true

    if (!background) {
      setIsLoading(true)
      setError('')
      setDeploymentHistoryUnavailable(false)
      setDeploymentHistoryError('')
    }

    try {
      const identity = await getServiceIdentity(decodedServiceId).catch(() =>
        createServiceIdentity({ serviceId: decodedServiceId }),
      )
      setServiceIdentity(identity)

      const [serviceResult, projectsResult, deploymentsResult, releasesResult, catalogResult] = await Promise.allSettled([
        getService(decodedServiceId),
        getProjects(),
        getDeploymentHistory(identity, { limit: 20 }),
        getReleaseTraceability({ serviceId: decodedServiceId, limit: 20 }),
        getCatalogReconciliation(),
      ])

      const fallback =
        projectsResult.status === 'fulfilled'
          ? buildFromProjects(decodedServiceId, projectsResult.value.projects)
          : buildFromProjects(decodedServiceId, [])

      if (serviceResult.status === 'fulfilled') {
        setServiceIdentity(buildIdentityFromServiceDetails(decodedServiceId, serviceResult.value, identity))
        setCapabilities(serviceResult.value.capabilities ?? null)
      } else {
        setCapabilities(null)
      }

      const releaseFallback =
        releasesResult.status === 'fulfilled'
          ? buildOverviewFromReleaseRows(decodedServiceId, releasesResult.value)
          : {}

      const finalOverview: ServiceOverviewData =
        serviceResult.status === 'fulfilled'
          ? {
              id: serviceResult.value.id || decodedServiceId,
              name: serviceResult.value.name || decodedServiceId,
              version: serviceResult.value.version ?? releaseFallback.version ?? 'N/A',
              health:
                normalizeHealthStatus(serviceResult.value.health) === 'unknown'
                  ? (releaseFallback.health ?? 'unknown')
                  : normalizeHealthStatus(serviceResult.value.health),
              sync:
                normalizeSyncStatus(serviceResult.value.sync) === 'unknown'
                  ? (releaseFallback.sync ?? 'unknown')
                  : normalizeSyncStatus(serviceResult.value.sync),
              endpoints: buildEndpointList(
                serviceResult.value.endpoints,
                serviceResult.value.publicUrl,
                serviceResult.value.internalUrls,
              ),
              endpointState: 'no_routed_endpoint',
              deployments: releaseFallback.deployments ?? [],
              deploymentLock: serviceResult.value.deploymentLock ?? null,
              observabilityMode: serviceResult.value.observabilityMode,
              publicHost: serviceResult.value.publicHost ?? undefined,
            }
          : fallback

      if (serviceResult.status !== 'fulfilled') {
        finalOverview.version = releaseFallback.version ?? finalOverview.version
        finalOverview.health = releaseFallback.health ?? finalOverview.health
        finalOverview.sync = releaseFallback.sync ?? finalOverview.sync
        if ((finalOverview.deployments?.length ?? 0) === 0 && releaseFallback.deployments?.length) {
          finalOverview.deployments = releaseFallback.deployments
        }
      }

      if (finalOverview.endpoints.length === 0 && fallback.endpoints.length > 0) {
        finalOverview.endpoints = fallback.endpoints
        finalOverview.endpointState = 'available'
      } else if (finalOverview.endpoints.length > 0) {
        finalOverview.endpointState = 'available'
      } else if (serviceResult.status !== 'fulfilled') {
        finalOverview.endpointState = fallback.endpointState
      }

      if (deploymentsResult.status === 'fulfilled') {
        setDeploymentHistory(deploymentsResult.value)
        finalOverview.deployments = deploymentsResult.value.map((deployment) => ({
          id: deployment.id,
          version: deployment.version,
          status: deployment.outcome,
          deployedAt: deployment.deployedAt,
        }))
        if (finalOverview.deployments.length === 0 && releaseFallback.deployments?.length) {
          finalOverview.deployments = releaseFallback.deployments
        }
      } else if (releaseFallback.deployments?.length) {
        setDeploymentHistory([])
        finalOverview.deployments = releaseFallback.deployments
      } else {
        setDeploymentHistory([])
        setDeploymentHistoryUnavailable(true)
        setDeploymentHistoryError(
          deploymentsResult.reason instanceof Error
            ? deploymentsResult.reason.message
            : 'Deployment history is unavailable right now.',
        )
      }

      setOverview(finalOverview)

      if (serviceResult.status === 'fulfilled' && serviceResult.value.projectContext) {
        setProjectContext(serviceResult.value.projectContext)
      } else if (catalogResult.status === 'fulfilled') {
        const matchingRow = catalogResult.value.rows.find((row: CatalogJoinRow) =>
          row.serviceIds.includes(decodedServiceId) || row.primaryServiceId === decodedServiceId,
        )
        if (matchingRow) {
          setProjectContext({
            projectId: matchingRow.projectId,
            projectName: matchingRow.projectName,
            namespace: matchingRow.namespace,
            siblingServiceIds: matchingRow.serviceIds.filter((sid: string) => sid !== decodedServiceId),
            isLinked: true,
          })
        } else {
          setProjectContext(null)
        }
      } else {
        setProjectContext(null)
      }
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load service overview'

      if (!background) {
        setDeploymentHistory([])
        setError(message)
      }
    } finally {
      if (!background) {
        setIsLoading(false)
      }
    }
  }, [decodedServiceId])

  useEffect(() => {
    void loadOverview()
  }, [loadOverview])

  const loadDeploymentInfo = useCallback(async () => {
    setDeploymentInfoLoading(true)
    setDeploymentInfoError('')

    try {
      const response = await getServiceDeploymentInfo(decodedServiceId, serviceIdentity.env || undefined)
      setDeploymentInfo(response)
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load deployment info'
      setDeploymentInfoError(message)
      setDeploymentInfo(null)
    } finally {
      setDeploymentInfoLoading(false)
    }
  }, [decodedServiceId, serviceIdentity.env])

  useEffect(() => {
    void loadDeploymentInfo()
  }, [loadDeploymentInfo])

  useEffect(() => {
    const interval = window.setInterval(() => {
      void loadOverview({ background: true })
      void loadDeploymentInfo()
    }, 30000)
    return () => window.clearInterval(interval)
  }, [loadDeploymentInfo, loadOverview])

  return {
    decodedServiceId,
    serviceIdentity,
    overview,
    projectContext,
    capabilities,
    deploymentHistory,
    deploymentInfo,
    deploymentInfoLoading,
    deploymentInfoError,
    isLoading,
    error,
    deploymentHistoryUnavailable,
    deploymentHistoryError,
    loadOverview,
    loadDeploymentInfo,
  }
}
