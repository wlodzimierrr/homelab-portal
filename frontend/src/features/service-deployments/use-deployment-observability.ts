import { useCallback, useEffect, useState } from 'react'
import {
  getDeploymentObservability,
  type DeploymentObservability,
} from '@/lib/adapters/deployment-observability'
import type { DeploymentHistoryItem } from '@/lib/adapters/deployments'
import type { ServiceIdentity } from '@/lib/service-identity'
import {
  buildDeploymentObservabilityRequest,
  type DeploymentLogsPreset,
} from './shared'

export function useDeploymentObservability(
  serviceIdentity: ServiceIdentity,
  selectedDeployment: DeploymentHistoryItem | null,
) {
  const [observability, setObservability] = useState<DeploymentObservability | null>(null)
  const [observabilityLoading, setObservabilityLoading] = useState(false)
  const [observabilityError, setObservabilityError] = useState('')
  const [logsPreset, setLogsPreset] = useState<DeploymentLogsPreset>('errors')

  // Observability is loaded independently from deployment history so selecting a
  // different row or changing the log preset does not refetch the full timeline.
  const loadDeploymentObservability = useCallback(async () => {
    if (!selectedDeployment) {
      setObservability(null)
      setObservabilityError('')
      return
    }

    setObservabilityLoading(true)
    setObservabilityError('')

    try {
      const response = await getDeploymentObservability(serviceIdentity, {
        ...buildDeploymentObservabilityRequest(selectedDeployment),
        logsPreset,
        logsLimit: 50,
      })
      setObservability(response)
    } catch (requestError) {
      const message =
        requestError instanceof Error
          ? requestError.message
          : 'Failed to load deployment-scoped observability.'
      setObservabilityError(message)
      setObservability(null)
    } finally {
      setObservabilityLoading(false)
    }
  }, [logsPreset, selectedDeployment, serviceIdentity])

  useEffect(() => {
    void loadDeploymentObservability()
  }, [loadDeploymentObservability])

  return {
    observability,
    observabilityLoading,
    observabilityError,
    logsPreset,
    setLogsPreset,
    loadDeploymentObservability,
  }
}
