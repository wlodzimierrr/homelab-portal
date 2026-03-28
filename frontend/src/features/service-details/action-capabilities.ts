import type { NormalizedServiceCapabilities } from './normalizers/service-detail-normalizer'

export interface ResolvedServiceActionCapabilities {
  canDeployToDev: boolean
  canPromoteToProd: boolean
  canRollback: boolean
  rollbackEnvs: Array<'dev' | 'prod'>
}

export function resolveServiceActionCapabilities(
  capabilities: NormalizedServiceCapabilities,
): ResolvedServiceActionCapabilities {
  return {
    canDeployToDev: capabilities.canDeployToDev,
    canPromoteToProd: capabilities.canPromoteToProd,
    canRollback: capabilities.canRollback,
    rollbackEnvs: capabilities.canRollback ? capabilities.rollbackEnvs : [],
  }
}
