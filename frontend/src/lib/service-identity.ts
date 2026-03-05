export interface ServiceIdentity {
  serviceId: string
  serviceName: string
  namespace: string
  env: string
  appLabel: string
  argoAppName?: string
}

const DEFAULT_NAMESPACE = 'default'
const DEFAULT_ENV = 'dev'

function safeTrim(value: string | undefined, fallback: string) {
  const normalized = value?.trim()
  return normalized ? normalized : fallback
}

export function parseNamespaceFromInternalUrl(url?: string) {
  if (!url) {
    return undefined
  }

  try {
    const parsed = new URL(url)
    const parts = parsed.hostname.split('.')
    if (parts.length >= 3 && parts[2] === 'svc' && parts[1]) {
      return parts[1]
    }
  } catch {
    // Internal URL may be non-standard; keep fallback behavior.
  }

  return undefined
}

export function normalizeServiceId(value: string | undefined) {
  const trimmed = value?.trim().toLowerCase() ?? ''
  if (!trimmed) {
    return ''
  }

  const normalized = trimmed
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+/, '')
    .replace(/-+$/, '')
    .replace(/-+/g, '-')

  return normalized || trimmed
}

export function createServiceIdentity(input: Partial<ServiceIdentity> & { serviceId: string }): ServiceIdentity {
  const canonicalServiceId = normalizeServiceId(input.serviceId)
  const serviceId = safeTrim(canonicalServiceId, 'unknown-service')
  const env = safeTrim(input.env, DEFAULT_ENV)
  const serviceName = safeTrim(input.serviceName, serviceId)
  const namespace = safeTrim(input.namespace, DEFAULT_NAMESPACE)
  const appLabel = safeTrim(input.appLabel, serviceId)
  const argoAppName = input.argoAppName?.trim() || `${serviceId}-${env}`

  return {
    serviceId,
    serviceName,
    namespace,
    env,
    appLabel,
    argoAppName,
  }
}
