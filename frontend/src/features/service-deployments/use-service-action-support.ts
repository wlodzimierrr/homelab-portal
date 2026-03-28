import { useCallback, useEffect, useMemo, useState } from 'react'
import { getService } from '@/lib/api/catalog'
import type { ServiceDeploymentLock } from '@/lib/api/deployments'
import { safeDecodeServiceId } from '@/features/service-details/shared'
import {
  normalizeServiceDetail,
  type NormalizedServiceCapabilities,
} from '@/features/service-details/normalizers/service-detail-normalizer'

export function useServiceActionSupport(serviceId: string) {
  const decodedServiceId = useMemo(() => safeDecodeServiceId(serviceId), [serviceId])
  const [deploymentLock, setDeploymentLock] = useState<ServiceDeploymentLock | null>(null)
  const [capabilities, setCapabilities] = useState<NormalizedServiceCapabilities>(() =>
    normalizeServiceDetail({ serviceId: decodedServiceId }).capabilities,
  )

  const loadServiceActionSupport = useCallback(async (options?: { background?: boolean }) => {
    try {
      const details = await getService(decodedServiceId)
      setDeploymentLock(details.deploymentLock ?? null)
      setCapabilities(
        normalizeServiceDetail({
          serviceId: decodedServiceId,
          serviceDetail: details,
        }).capabilities,
      )
    } catch {
      if (!options?.background) {
        setDeploymentLock(null)
        setCapabilities(normalizeServiceDetail({ serviceId: decodedServiceId }).capabilities)
      }
    }
  }, [decodedServiceId])

  useEffect(() => {
    let cancelled = false

    async function loadInitialServiceActionSupport() {
      try {
        const details = await getService(decodedServiceId)
        if (cancelled) {
          return
        }
        setDeploymentLock(details.deploymentLock ?? null)
        setCapabilities(
          normalizeServiceDetail({
            serviceId: decodedServiceId,
            serviceDetail: details,
          }).capabilities,
        )
      } catch {
        if (cancelled) {
          return
        }
        setDeploymentLock(null)
        setCapabilities(normalizeServiceDetail({ serviceId: decodedServiceId }).capabilities)
      }
    }

    void loadInitialServiceActionSupport()

    return () => {
      cancelled = true
    }
  }, [decodedServiceId])

  return {
    decodedServiceId,
    deploymentLock,
    capabilities,
    loadServiceActionSupport,
  }
}
