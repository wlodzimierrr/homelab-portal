import { useCallback, useEffect, useMemo, useState } from 'react'
import { getService, type ServiceCapabilities } from '@/lib/api/catalog'
import type { ServiceDeploymentLock } from '@/lib/api/deployments'
import { safeDecodeServiceId } from '@/features/service-details/shared'

export function useServiceActionSupport(serviceId: string) {
  const decodedServiceId = useMemo(() => safeDecodeServiceId(serviceId), [serviceId])
  const [deploymentLock, setDeploymentLock] = useState<ServiceDeploymentLock | null>(null)
  const [capabilities, setCapabilities] = useState<ServiceCapabilities | null>(null)

  const loadServiceActionSupport = useCallback(async (options?: { background?: boolean }) => {
    try {
      const details = await getService(decodedServiceId)
      setDeploymentLock(details.deploymentLock ?? null)
      setCapabilities(details.capabilities ?? null)
    } catch {
      if (!options?.background) {
        setDeploymentLock(null)
        setCapabilities(null)
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
        setCapabilities(details.capabilities ?? null)
      } catch {
        if (cancelled) {
          return
        }
        setDeploymentLock(null)
        setCapabilities(null)
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
