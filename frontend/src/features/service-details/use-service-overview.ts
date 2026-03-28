import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getCatalogReconciliation,
  type CatalogJoinRow,
  getProjects,
  getService,
  type ServiceDetails,
} from '@/lib/api/catalog'
import { getReleaseTraceability } from '@/lib/api/deployments'
import { getDeploymentHistory, type DeploymentHistoryItem } from '@/lib/adapters/deployments'
import { createServiceIdentity, type ServiceIdentity } from '@/lib/service-identity'
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
import {
  normalizeServiceDetail,
  type NormalizedServiceCapabilities,
} from './normalizers/service-detail-normalizer'

function hasServiceEndpointMetadata(details: ServiceDetails) {
  return Boolean(
    details.publicUrl ||
    details.internalUrls?.length ||
    details.endpoints?.some((endpoint) => Boolean(endpoint.url)),
  )
}

function needsReleaseFallback(details: ServiceDetails) {
  return (
    !details.version ||
    normalizeHealthStatus(details.health) === 'unknown' ||
    normalizeSyncStatus(details.sync) === 'unknown'
  )
}

function buildIdentityFromCatalogRow(
  serviceId: string,
  row: CatalogJoinRow | undefined,
  fallback: ServiceIdentity,
) {
  if (!row) {
    return fallback
  }

  const matchingService = row.services.find((service) => service.serviceId === serviceId)

  return createServiceIdentity({
    serviceId,
    serviceName: matchingService?.serviceName ?? fallback.serviceName ?? serviceId,
    namespace: matchingService?.namespace ?? row.namespace ?? fallback.namespace,
    env: row.env || fallback.env,
    appLabel: matchingService?.appLabel ?? row.appLabel ?? fallback.appLabel,
    argoAppName: matchingService?.argoAppName ?? fallback.argoAppName,
  })
}

export function useServiceOverview(serviceId: string) {
  const decodedServiceId = useMemo(() => safeDecodeServiceId(serviceId), [serviceId])
  const [serviceIdentity, setServiceIdentity] = useState<ServiceIdentity>(() =>
    createServiceIdentity({ serviceId: decodedServiceId }),
  )
  const [overview, setOverview] = useState<ServiceOverviewData | null>(null)
  const [deploymentHistory, setDeploymentHistory] = useState<DeploymentHistoryItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [projectContext, setProjectContext] = useState(() =>
    normalizeServiceDetail({ serviceId: decodedServiceId }).projectContext,
  )
  const [capabilities, setCapabilities] = useState<NormalizedServiceCapabilities>(() =>
    normalizeServiceDetail({ serviceId: decodedServiceId }).capabilities,
  )

  const loadOverview = useCallback(async (options?: { background?: boolean }) => {
    const background = options?.background === true

    if (!background) {
      setIsLoading(true)
      setError('')
    }

    try {
      const fallbackIdentity = createServiceIdentity({ serviceId: decodedServiceId })
      const serviceResult = await Promise.allSettled([getService(decodedServiceId)]).then((results) => results[0])
      const baseIdentity =
        serviceResult.status === 'fulfilled'
          ? buildIdentityFromServiceDetails(decodedServiceId, serviceResult.value, fallbackIdentity)
          : fallbackIdentity
      setServiceIdentity(baseIdentity)

      const shouldLoadProjects =
        serviceResult.status !== 'fulfilled' ||
        !hasServiceEndpointMetadata(serviceResult.value)
      const shouldLoadReleaseFallback =
        serviceResult.status !== 'fulfilled' ||
        needsReleaseFallback(serviceResult.value)
      const shouldLoadCatalogFallback =
        serviceResult.status !== 'fulfilled' ||
        !serviceResult.value.projectContext

      const supplementalPromises = await Promise.allSettled([
        getDeploymentHistory(baseIdentity, { limit: 20 }),
        shouldLoadProjects ? getProjects() : Promise.resolve(null),
        shouldLoadReleaseFallback
          ? getReleaseTraceability({ serviceId: decodedServiceId, limit: 20 })
          : Promise.resolve(null),
        shouldLoadCatalogFallback ? getCatalogReconciliation() : Promise.resolve(null),
      ])

      const deploymentsResult = supplementalPromises[0]
      const projectsResult = supplementalPromises[1]
      const releasesResult = supplementalPromises[2]
      const catalogResult = supplementalPromises[3]

      const fallback =
        projectsResult.status === 'fulfilled' && projectsResult.value
          ? buildFromProjects(decodedServiceId, projectsResult.value.projects)
          : buildFromProjects(decodedServiceId, [])

      if (serviceResult.status === 'fulfilled') {
        setServiceIdentity(buildIdentityFromServiceDetails(decodedServiceId, serviceResult.value, baseIdentity))
      }

      const releaseFallback =
        releasesResult.status === 'fulfilled' && releasesResult.value
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
      }

      setOverview(finalOverview)

      const matchingCatalogRow =
        catalogResult.status === 'fulfilled' && catalogResult.value
          ? catalogResult.value.rows.find(
              (row: CatalogJoinRow) =>
                row.serviceIds.includes(decodedServiceId) || row.primaryServiceId === decodedServiceId,
            ) ?? null
          : null

      if (serviceResult.status !== 'fulfilled' && matchingCatalogRow) {
        setServiceIdentity((currentIdentity) =>
          buildIdentityFromCatalogRow(decodedServiceId, matchingCatalogRow, currentIdentity),
        )
      }

      const normalizedDetail = normalizeServiceDetail({
        serviceId: decodedServiceId,
        serviceDetail: serviceResult.status === 'fulfilled' ? serviceResult.value : null,
        catalogRow: matchingCatalogRow,
      })

      setProjectContext(normalizedDetail.projectContext)
      setCapabilities(normalizedDetail.capabilities)
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

  useEffect(() => {
    const interval = window.setInterval(() => {
      void loadOverview({ background: true })
    }, 30000)
    return () => window.clearInterval(interval)
  }, [loadOverview])

  return {
    decodedServiceId,
    serviceIdentity,
    overview,
    projectContext,
    capabilities,
    deploymentHistory,
    isLoading,
    error,
    loadOverview,
  }
}
