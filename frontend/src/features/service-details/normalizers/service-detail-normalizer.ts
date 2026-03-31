import type {
  CatalogJoinRow,
  ServiceCapabilities,
  ServiceDetails,
  ServiceProjectContext,
} from '@/lib/api/catalog'

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
  canDelete: boolean
  decommissionMode: 'standalone' | 'project-component' | 'unsupported'
  decommissionReason: string | null
}

export const EMPTY_SERVICE_CAPABILITIES: NormalizedServiceCapabilities = {
  canDeployToDev: false,
  canPromoteToProd: false,
  canRollback: false,
  rollbackEnvs: [],
  canEditConfig: false,
  configEnvs: [],
  canEditPublicHostname: false,
  canAdopt: true,
  canDelete: false,
  decommissionMode: 'unsupported',
  decommissionReason: null,
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
  capabilities?: ServiceCapabilities | null,
  projectContext?: ServiceProjectContext | null,
): NormalizedServiceCapabilities {
  const explicitRollbackEnvs = normalizeSupportedEnvironments(capabilities?.rollbackEnvs)
  const canRollback = capabilities?.canRollback ?? explicitRollbackEnvs.length > 0
  const rollbackEnvs = canRollback ? explicitRollbackEnvs : []

  const explicitConfigEnvs = normalizeSupportedEnvironments(capabilities?.configEnvs)
  const canEditConfig = capabilities?.canEditConfig ?? explicitConfigEnvs.length > 0
  const configEnvs = canEditConfig ? explicitConfigEnvs : []

  return {
    canDeployToDev: capabilities?.canDeployToDev ?? EMPTY_SERVICE_CAPABILITIES.canDeployToDev,
    canPromoteToProd: capabilities?.canPromoteToProd ?? EMPTY_SERVICE_CAPABILITIES.canPromoteToProd,
    canRollback,
    rollbackEnvs,
    canEditConfig,
    configEnvs,
    canEditPublicHostname:
      capabilities?.canEditPublicHostname ?? EMPTY_SERVICE_CAPABILITIES.canEditPublicHostname,
    canAdopt: capabilities?.canAdopt ?? !projectContext?.isLinked,
    canDelete: capabilities?.canDelete ?? EMPTY_SERVICE_CAPABILITIES.canDelete,
    decommissionMode:
      capabilities?.decommissionMode ?? EMPTY_SERVICE_CAPABILITIES.decommissionMode,
    decommissionReason:
      capabilities?.decommissionReason ?? EMPTY_SERVICE_CAPABILITIES.decommissionReason,
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
    capabilities: normalizeServiceCapabilities(serviceDetail?.capabilities, projectContext),
  }
}
