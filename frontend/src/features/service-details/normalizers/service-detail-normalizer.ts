import type {
  CatalogJoinRow,
  ServiceCapabilities,
  ServiceDetails,
  ServiceProjectContext,
} from '@/lib/api/catalog'
import { normalizeServiceId } from '@/lib/service-identity'

type SupportedEnvironment = 'dev' | 'prod'

export interface NormalizedServiceCapabilities {
  canDeployToDev: boolean
  canPromoteToProd: boolean
  canRollback: boolean
  rollbackEnvs: SupportedEnvironment[]
  canEditConfig: boolean
  configEnvs: SupportedEnvironment[]
  canEditPublicHostname: boolean
  canAdopt: boolean
}

export interface NormalizedServiceDetail {
  projectContext: ServiceProjectContext | null
  capabilities: NormalizedServiceCapabilities
}

interface NormalizeServiceDetailOptions {
  serviceId: string
  serviceDetail?: Pick<ServiceDetails, 'projectContext' | 'capabilities'> | null
  catalogRow?: CatalogJoinRow | null
}

function supportsServiceDeployToDevFallback(serviceId: string) {
  const normalized = normalizeServiceId(serviceId)
  return normalized === 'homelab-api' || normalized === 'homelab-web'
}

function supportsServicePromoteToProdFallback(serviceId: string) {
  const normalized = normalizeServiceId(serviceId)
  return normalized === 'homelab-api' || normalized === 'homelab-web'
}

function supportsServiceRollbackFallback(serviceId: string) {
  const normalized = normalizeServiceId(serviceId)
  return normalized === 'homelab-api' || normalized === 'homelab-web'
}

function supportsConfigEditingFallback(serviceId: string) {
  return normalizeServiceId(serviceId) === 'homelab-api'
}

function normalizeSupportedEnvironments(value?: string[] | null): SupportedEnvironment[] {
  return (value ?? []).filter((env): env is SupportedEnvironment => env === 'dev' || env === 'prod')
}

function normalizeProjectContextFromBackend(
  serviceId: string,
  projectContext?: ServiceProjectContext | null,
): ServiceProjectContext | null {
  if (!projectContext) {
    return null
  }

  return {
    projectId: projectContext.projectId ?? null,
    projectName: projectContext.projectName ?? null,
    namespace: projectContext.namespace,
    siblingServiceIds: (projectContext.siblingServiceIds ?? []).filter((sid) => sid !== serviceId),
    isLinked: projectContext.isLinked,
  }
}

function normalizeProjectContextFromCatalog(
  serviceId: string,
  catalogRow?: CatalogJoinRow | null,
): ServiceProjectContext | null {
  if (!catalogRow) {
    return null
  }

  return {
    projectId: catalogRow.projectId,
    projectName: catalogRow.projectName,
    namespace: catalogRow.namespace,
    siblingServiceIds: catalogRow.serviceIds.filter((sid) => sid !== serviceId),
    isLinked: true,
  }
}

export function normalizeServiceProjectContext({
  serviceId,
  serviceDetail,
  catalogRow,
}: NormalizeServiceDetailOptions): ServiceProjectContext | null {
  return (
    normalizeProjectContextFromBackend(serviceId, serviceDetail?.projectContext) ??
    normalizeProjectContextFromCatalog(serviceId, catalogRow)
  )
}

export function normalizeServiceCapabilities(
  serviceId: string,
  capabilities?: ServiceCapabilities | null,
  projectContext?: ServiceProjectContext | null,
): NormalizedServiceCapabilities {
  const explicitRollbackEnvs = normalizeSupportedEnvironments(capabilities?.rollbackEnvs)
  const canRollback =
    capabilities?.canRollback ??
    (explicitRollbackEnvs.length > 0 ? true : supportsServiceRollbackFallback(serviceId))
  const rollbackEnvs =
    explicitRollbackEnvs.length > 0
      ? explicitRollbackEnvs
      : canRollback
        ? (['dev', 'prod'] as SupportedEnvironment[])
        : []

  const explicitConfigEnvs = normalizeSupportedEnvironments(capabilities?.configEnvs)
  const canEditConfig = capabilities?.canEditConfig ?? supportsConfigEditingFallback(serviceId)
  const configEnvs =
    explicitConfigEnvs.length > 0
      ? explicitConfigEnvs
      : canEditConfig
        ? (['dev', 'prod'] as SupportedEnvironment[])
        : []

  return {
    canDeployToDev: capabilities?.canDeployToDev ?? supportsServiceDeployToDevFallback(serviceId),
    canPromoteToProd:
      capabilities?.canPromoteToProd ?? supportsServicePromoteToProdFallback(serviceId),
    canRollback,
    rollbackEnvs,
    canEditConfig,
    configEnvs,
    // Keep permissive legacy defaults until the backend contract is universally present.
    canEditPublicHostname: capabilities?.canEditPublicHostname ?? true,
    canAdopt: capabilities?.canAdopt ?? !projectContext?.isLinked,
  }
}

export function normalizeServiceDetail({
  serviceId,
  serviceDetail,
  catalogRow,
}: NormalizeServiceDetailOptions): NormalizedServiceDetail {
  const projectContext = normalizeServiceProjectContext({ serviceId, serviceDetail, catalogRow })

  return {
    projectContext,
    capabilities: normalizeServiceCapabilities(serviceId, serviceDetail?.capabilities, projectContext),
  }
}
