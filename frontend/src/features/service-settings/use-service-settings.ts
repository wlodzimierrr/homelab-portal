import { useCallback, useEffect, useState } from 'react'
import { adoptService, decommissionService, type ServiceDecommissionResponse } from '@/lib/api'
import {
  getServiceConfig,
  setServiceConfig,
  updateServicePublicHostname,
  type ServiceConfigEntry,
  type ServiceSetConfigResponse,
  type UpdatePublicHostnameResponse,
} from '@/lib/api/admin'
import { ApiRequestError } from '@/lib/http/errors'
import type { NormalizedServiceCapabilities } from '@/features/service-details/normalizers/service-detail-normalizer'

interface UseServiceSettingsOptions {
  serviceId: string
  initialPublicHost?: string
  capabilities: NormalizedServiceCapabilities
  refreshOverview: (options?: { background?: boolean }) => Promise<void>
}

export function useServiceSettings({
  serviceId,
  initialPublicHost,
  capabilities,
  refreshOverview,
}: UseServiceSettingsOptions) {
  const configSupported = capabilities.canEditConfig
  const adoptSupported = capabilities.canAdopt
  const decommissionSupported = capabilities.decommissionMode !== 'unsupported'

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

  const [adoptProjectId, setAdoptProjectId] = useState('')
  const [adoptSubmitting, setAdoptSubmitting] = useState(false)
  const [adoptError, setAdoptError] = useState('')
  const [adoptResult, setAdoptResult] = useState<{ status: string; message: string; prUrl?: string } | null>(null)

  const [decommissionConfirmation, setDecommissionConfirmation] = useState('')
  const [decommissionSubmitting, setDecommissionSubmitting] = useState(false)
  const [decommissionError, setDecommissionError] = useState('')
  const [decommissionResult, setDecommissionResult] = useState<ServiceDecommissionResponse | null>(null)

  useEffect(() => {
    setPublicHostValue(initialPublicHost ?? '')
  }, [initialPublicHost])

  const loadConfig = useCallback(
    async (env: 'dev' | 'prod') => {
      setConfigLoading(true)
      setConfigError('')
      setConfigSubmitResult(null)
      try {
        const result = await getServiceConfig(serviceId, env)
        setConfigEntries(result.entries)
        const initialValues: Record<string, string> = {}
        for (const entry of result.entries) {
          initialValues[entry.key] = entry.value
        }
        setConfigSelectedValues(initialValues)
      } catch (err) {
        setConfigError(err instanceof Error ? err.message : 'Failed to load config.')
      } finally {
        setConfigLoading(false)
      }
    },
    [serviceId],
  )

  useEffect(() => {
    if (configSupported) {
      void loadConfig(configEnv)
    }
  }, [configEnv, configSupported, loadConfig])

  const submitConfigEdit = useCallback(
    async (key: string) => {
      const value = configSelectedValues[key]
      if (!value) {
        return null
      }

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
    },
    [configEnv, configSelectedValues, loadConfig, serviceId],
  )

  const submitPublicHostname = useCallback(async () => {
    const trimmed = publicHostValue.trim()
    if (!trimmed) {
      return null
    }

    setPublicHostSubmitting(true)
    setPublicHostError('')
    setPublicHostResult(null)
    try {
      const result = await updateServicePublicHostname(serviceId, trimmed)
      setPublicHostResult(result)
      setPublicHostEditMode(false)
      await refreshOverview({ background: true })
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
  }, [publicHostValue, refreshOverview, serviceId])

  const submitAdopt = useCallback(async () => {
    const trimmedProjectId = adoptProjectId.trim()
    if (!trimmedProjectId) {
      return null
    }

    setAdoptSubmitting(true)
    setAdoptError('')
    setAdoptResult(null)
    try {
      const response = await adoptService(serviceId, trimmedProjectId)
      setAdoptResult({
        status: response.status,
        message: response.message,
        prUrl: response.prUrl ?? undefined,
      })
      return response
    } catch (err) {
      setAdoptError(err instanceof Error ? err.message : 'Failed to adopt service.')
      throw err
    } finally {
      setAdoptSubmitting(false)
    }
  }, [adoptProjectId, serviceId])

  const submitDecommission = useCallback(async () => {
    if (!decommissionSupported || decommissionConfirmation.trim() !== serviceId) {
      return null
    }

    setDecommissionSubmitting(true)
    setDecommissionError('')
    setDecommissionResult(null)
    try {
      const response = await decommissionService(serviceId)
      setDecommissionResult(response)
      return response
    } catch (err) {
      setDecommissionError(err instanceof Error ? err.message : 'Failed to start decommission flow.')
      throw err
    } finally {
      setDecommissionSubmitting(false)
    }
  }, [decommissionConfirmation, decommissionSupported, serviceId])

  return {
    configSupported,
    adoptSupported,
    decommissionSupported,
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
    loadConfig,
    submitConfigEdit,
    publicHostEditMode,
    setPublicHostEditMode,
    publicHostValue,
    setPublicHostValue,
    publicHostSubmitting,
    publicHostError,
    publicHostResult,
    submitPublicHostname,
    adoptProjectId,
    setAdoptProjectId,
    adoptSubmitting,
    adoptError,
    adoptResult,
    submitAdopt,
    decommissionConfirmation,
    setDecommissionConfirmation,
    decommissionSubmitting,
    decommissionError,
    decommissionResult,
    submitDecommission,
  }
}
