import type { ServiceCapabilities } from '@/lib/api/catalog'
import { normalizeServiceId } from '@/lib/service-identity'
import { supportsServiceRollback } from './shared'

function supportsServiceDeployToDevFallback(serviceId: string) {
  const normalized = normalizeServiceId(serviceId)
  return normalized === 'homelab-api' || normalized === 'homelab-web'
}

function supportsServicePromoteToProdFallback(serviceId: string) {
  const normalized = normalizeServiceId(serviceId)
  return normalized === 'homelab-api' || normalized === 'homelab-web'
}

function normalizeRollbackEnvs(value: string[] | undefined, serviceId: string) {
  const normalized = (value ?? []).filter((env): env is 'dev' | 'prod' => env === 'dev' || env === 'prod')
  if (normalized.length > 0) {
    return normalized
  }
  return supportsServiceRollback(serviceId) ? (['dev', 'prod'] as Array<'dev' | 'prod'>) : []
}

export interface ResolvedServiceActionCapabilities {
  canDeployToDev: boolean
  canPromoteToProd: boolean
  canRollback: boolean
  rollbackEnvs: Array<'dev' | 'prod'>
}

export function resolveServiceActionCapabilities(
  serviceId: string,
  capabilities?: ServiceCapabilities | null,
): ResolvedServiceActionCapabilities {
  const explicitRollbackEnvs = (capabilities?.rollbackEnvs ?? []).filter(
    (env): env is 'dev' | 'prod' => env === 'dev' || env === 'prod',
  )
  const rollbackEnvs =
    explicitRollbackEnvs.length > 0
      ? explicitRollbackEnvs
      : capabilities?.canRollback
        ? (['dev', 'prod'] as Array<'dev' | 'prod'>)
        : normalizeRollbackEnvs(undefined, serviceId)
  const canRollback = capabilities?.canRollback ?? rollbackEnvs.length > 0

  return {
    canDeployToDev: capabilities?.canDeployToDev ?? supportsServiceDeployToDevFallback(serviceId),
    canPromoteToProd:
      capabilities?.canPromoteToProd ?? supportsServicePromoteToProdFallback(serviceId),
    canRollback,
    rollbackEnvs: canRollback ? rollbackEnvs : [],
  }
}
