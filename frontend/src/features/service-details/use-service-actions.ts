import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  getServiceRollbackCandidates,
  requestServiceDeployToDev,
  requestServicePromoteToProd,
  requestServiceRollback,
  type ServiceDeployToDevResponse,
  type ServiceDeploymentLock,
  type ServicePromoteToProdResponse,
  type ServiceRollbackCandidatesResponse,
  type ServiceRollbackResponse,
} from '@/lib/api/deployments'
import {
  getServiceConfig,
  setServiceConfig,
  updateServicePublicHostname,
  type ServiceConfigEntry,
  type ServiceSetConfigResponse,
  type UpdatePublicHostnameResponse,
} from '@/lib/api/admin'
import { ApiRequestError } from '@/lib/http/errors'
import type { DeploymentHistoryItem } from '@/lib/adapters/deployments'
import {
  getLatestDeploymentForEnv,
  getRecentDeploymentTags,
  supportsConfigEditing,
  supportsServiceRollback,
} from './shared'

interface UseServiceActionsOptions {
  serviceId: string
  serviceEnv?: string
  deploymentHistory: DeploymentHistoryItem[]
  deploymentLock?: ServiceDeploymentLock | null
  initialPublicHost?: string
  refreshOverview: (options?: { background?: boolean }) => Promise<void>
  refreshDeploymentInfo: () => Promise<void>
}

export function useServiceActions({
  serviceId,
  serviceEnv,
  deploymentHistory,
  deploymentLock,
  initialPublicHost,
  refreshOverview,
  refreshDeploymentInfo,
}: UseServiceActionsOptions) {
  const rollbackSupported = useMemo(() => supportsServiceRollback(serviceId), [serviceId])
  const configSupported = useMemo(() => supportsConfigEditing(serviceId), [serviceId])
  const latestDevDeployment = useMemo(
    () => getLatestDeploymentForEnv(deploymentHistory, 'dev'),
    [deploymentHistory],
  )
  const latestProdDeployment = useMemo(
    () => getLatestDeploymentForEnv(deploymentHistory, 'prod'),
    [deploymentHistory],
  )
  const recentDevTags = useMemo(() => getRecentDeploymentTags(deploymentHistory, 'dev'), [deploymentHistory])
  const recentProdTags = useMemo(() => getRecentDeploymentTags(deploymentHistory, 'prod'), [deploymentHistory])
  const devInFlight = useMemo(
    () =>
      deploymentHistory.some(
        (deployment) =>
          deployment.identity.env === 'dev' &&
          (deployment.outcome === 'pending' || deployment.outcome === 'deploying'),
      ),
    [deploymentHistory],
  )
  const prodInFlight = useMemo(
    () =>
      deploymentHistory.some(
        (deployment) =>
          deployment.identity.env === 'prod' &&
          (deployment.outcome === 'pending' || deployment.outcome === 'deploying'),
      ),
    [deploymentHistory],
  )
  const devLockActive = deploymentLock?.env === 'dev'
  const prodLockActive = deploymentLock?.env === 'prod'

  const [rollbackTargetEnvironment, setRollbackTargetEnvironment] = useState<'dev' | 'prod'>('dev')
  const [rollbackCandidates, setRollbackCandidates] = useState<ServiceRollbackCandidatesResponse | null>(null)
  const [rollbackCandidatesLoading, setRollbackCandidatesLoading] = useState(false)
  const [rollbackCandidatesError, setRollbackCandidatesError] = useState('')
  const [selectedRollbackTag, setSelectedRollbackTag] = useState('')
  const [rollbackReason, setRollbackReason] = useState('')
  const [rollbackSubmitting, setRollbackSubmitting] = useState(false)
  const [rollbackError, setRollbackError] = useState('')
  const [rollbackResult, setRollbackResult] = useState<ServiceRollbackResponse | null>(null)
  const [deployReason, setDeployReason] = useState('')
  const [deploySubmitting, setDeploySubmitting] = useState(false)
  const [deployError, setDeployError] = useState('')
  const [deployResult, setDeployResult] = useState<ServiceDeployToDevResponse | null>(null)
  const [promoteReason, setPromoteReason] = useState('')
  const [promoteSubmitting, setPromoteSubmitting] = useState(false)
  const [promoteError, setPromoteError] = useState('')
  const [promoteResult, setPromoteResult] = useState<ServicePromoteToProdResponse | null>(null)
  const [configEnv, setConfigEnv] = useState<'dev' | 'prod'>('dev')
  const [configEntries, setConfigEntries] = useState<ServiceConfigEntry[]>([])
  const [configLoading, setConfigLoading] = useState(false)
  const [configError, setConfigError] = useState('')
  const [configSelectedValues, setConfigSelectedValues] = useState<Record<string, string>>({})
  const [configSubmitting, setConfigSubmitting] = useState(false)
  const [configSubmitError, setConfigSubmitError] = useState('')
  const [configSubmitResult, setConfigSubmitResult] = useState<ServiceSetConfigResponse | null>(null)
  const [publicHostEditMode, setPublicHostEditMode] = useState(false)
  const [publicHostValue, setPublicHostValue] = useState(initialPublicHost ?? '')
  const [publicHostSubmitting, setPublicHostSubmitting] = useState(false)
  const [publicHostError, setPublicHostError] = useState('')
  const [publicHostResult, setPublicHostResult] = useState<UpdatePublicHostnameResponse | null>(null)

  useEffect(() => {
    if (serviceEnv === 'dev' || serviceEnv === 'prod') {
      setRollbackTargetEnvironment(serviceEnv)
    }
  }, [serviceEnv])

  useEffect(() => {
    setPublicHostValue(initialPublicHost ?? '')
  }, [initialPublicHost])

  const rollbackLockActive = rollbackTargetEnvironment === 'dev' ? devLockActive : prodLockActive
  const rollbackInFlight = rollbackTargetEnvironment === 'dev' ? devInFlight : prodInFlight

  const loadRollbackCandidates = useCallback(async () => {
    if (!rollbackSupported) {
      setRollbackCandidates(null)
      setRollbackCandidatesError('')
      setSelectedRollbackTag('')
      return
    }

    setRollbackCandidatesLoading(true)
    setRollbackCandidatesError('')

    try {
      const response = await getServiceRollbackCandidates(serviceId, rollbackTargetEnvironment)
      setRollbackCandidates(response)
      setSelectedRollbackTag((current) => {
        if (current && response.candidates.some((candidate) => candidate.tag === current)) {
          return current
        }
        return response.candidates[0]?.tag ?? ''
      })
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load rollback candidates.'
      setRollbackCandidatesError(message)
      setRollbackCandidates(null)
      setSelectedRollbackTag('')
    } finally {
      setRollbackCandidatesLoading(false)
    }
  }, [rollbackSupported, rollbackTargetEnvironment, serviceId])

  useEffect(() => {
    void loadRollbackCandidates()
  }, [loadRollbackCandidates])

  const submitDeployRequest = useCallback(async () => {
    setDeploySubmitting(true)
    setDeployError('')
    setDeployResult(null)

    try {
      const response = await requestServiceDeployToDev(serviceId, {
        deployReason,
      })
      setDeployResult(response)
      setDeployReason('')
      await refreshOverview({ background: true })
      await refreshDeploymentInfo()
      return response
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to request deploy to dev.'
      setDeployError(message)
      throw requestError
    } finally {
      setDeploySubmitting(false)
    }
  }, [deployReason, refreshDeploymentInfo, refreshOverview, serviceId])

  const submitPromoteRequest = useCallback(async () => {
    setPromoteSubmitting(true)
    setPromoteError('')
    setPromoteResult(null)

    try {
      const response = await requestServicePromoteToProd(serviceId, {
        deployReason: promoteReason,
      })
      setPromoteResult(response)
      setPromoteReason('')
      await refreshOverview({ background: true })
      await refreshDeploymentInfo()
      return response
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to request promote to prod.'
      setPromoteError(message)
      throw requestError
    } finally {
      setPromoteSubmitting(false)
    }
  }, [promoteReason, refreshDeploymentInfo, refreshOverview, serviceId])

  const submitRollbackRequest = useCallback(async () => {
    setRollbackSubmitting(true)
    setRollbackError('')
    setRollbackResult(null)

    try {
      const response = await requestServiceRollback(serviceId, {
        targetEnvironment: rollbackTargetEnvironment,
        rollbackTag: selectedRollbackTag,
        deployReason: rollbackReason,
      })
      setRollbackResult(response)
      setRollbackReason('')
      await refreshOverview({ background: true })
      await refreshDeploymentInfo()
      return response
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to request service rollback.'
      setRollbackError(message)
      throw requestError
    } finally {
      setRollbackSubmitting(false)
    }
  }, [
    refreshDeploymentInfo,
    refreshOverview,
    rollbackReason,
    rollbackTargetEnvironment,
    selectedRollbackTag,
    serviceId,
  ])

  const loadConfig = useCallback(async (env: 'dev' | 'prod') => {
    setConfigLoading(true)
    setConfigError('')
    setConfigSubmitResult(null)
    try {
      const result = await getServiceConfig(serviceId, env)
      setConfigEntries(result.entries)
      const initial: Record<string, string> = {}
      for (const entry of result.entries) {
        initial[entry.key] = entry.value
      }
      setConfigSelectedValues(initial)
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : 'Failed to load config.')
    } finally {
      setConfigLoading(false)
    }
  }, [serviceId])

  useEffect(() => {
    if (configSupported) {
      void loadConfig(configEnv)
    }
  }, [configSupported, configEnv, loadConfig])

  const submitConfigEdit = useCallback(async (key: string) => {
    const value = configSelectedValues[key]
    if (!value) return null
    setConfigSubmitting(true)
    setConfigSubmitError('')
    setConfigSubmitResult(null)
    try {
      const result = await setServiceConfig(serviceId, {
        env: configEnv,
        configKey: key,
        configValue: value,
      })
      setConfigSubmitResult(result)
      await loadConfig(configEnv)
      return result
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 429) {
        setConfigSubmitError('Rate limited: config edits are limited to one every 30 seconds.')
      } else {
        setConfigSubmitError(err instanceof Error ? err.message : 'Failed to submit config change.')
      }
      throw err
    } finally {
      setConfigSubmitting(false)
    }
  }, [configEnv, configSelectedValues, loadConfig, serviceId])

  const submitPublicHostname = useCallback(async () => {
    const trimmed = publicHostValue.trim()
    if (!trimmed) return null
    setPublicHostSubmitting(true)
    setPublicHostError('')
    setPublicHostResult(null)
    try {
      const result = await updateServicePublicHostname(serviceId, trimmed)
      setPublicHostResult(result)
      setPublicHostEditMode(false)
      return result
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 204) {
        setPublicHostEditMode(false)
        return null
      }
      setPublicHostError(err instanceof Error ? err.message : 'Failed to update public hostname.')
      throw err
    } finally {
      setPublicHostSubmitting(false)
    }
  }, [publicHostValue, serviceId])

  return {
    rollbackSupported,
    configSupported,
    latestDevDeployment,
    latestProdDeployment,
    recentDevTags,
    recentProdTags,
    devInFlight,
    prodInFlight,
    devLockActive,
    prodLockActive,
    rollbackTargetEnvironment,
    setRollbackTargetEnvironment,
    rollbackCandidates,
    rollbackCandidatesLoading,
    rollbackCandidatesError,
    selectedRollbackTag,
    setSelectedRollbackTag,
    rollbackReason,
    setRollbackReason,
    rollbackSubmitting,
    rollbackError,
    rollbackResult,
    rollbackLockActive,
    rollbackInFlight,
    deployReason,
    setDeployReason,
    deploySubmitting,
    deployError,
    deployResult,
    promoteReason,
    setPromoteReason,
    promoteSubmitting,
    promoteError,
    promoteResult,
    submitDeployRequest,
    submitPromoteRequest,
    submitRollbackRequest,
    configEnv,
    setConfigEnv,
    configEntries,
    configLoading,
    configError,
    configSelectedValues,
    setConfigSelectedValues,
    configSubmitting,
    configSubmitError,
    configSubmitResult,
    submitConfigEdit,
    loadConfig,
    publicHostEditMode,
    setPublicHostEditMode,
    publicHostValue,
    setPublicHostValue,
    publicHostSubmitting,
    publicHostError,
    publicHostResult,
    submitPublicHostname,
  }
}
