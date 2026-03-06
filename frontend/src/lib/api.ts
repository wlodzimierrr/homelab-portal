import { clearToken, getToken } from '@/lib/auth'
import { config } from '@/lib/config'
import type { ServiceIdentity } from '@/lib/service-identity'

export const UNAUTHORIZED_EVENT = 'portal:unauthorized'
const serviceEndpointMissingStatuses = new Set([404, 405, 501])
const enableServiceApi = import.meta.env.VITE_ENABLE_SERVICE_API === 'true'

type ServiceApiAvailability = 'unknown' | 'available' | 'unavailable'
let serviceApiAvailability: ServiceApiAvailability = enableServiceApi ? 'unknown' : 'unavailable'

interface RequestOptions extends Omit<RequestInit, 'headers'> {
  headers?: HeadersInit
  skipUnauthorizedRedirect?: boolean
}

interface ApiErrorPayload {
  detail?:
    | string
    | {
        message?: string
        correlationId?: string
        providerStatus?: {
          provider?: string
          status?: string
        }
      }
  message?: string
}

export interface MonitoringProviderStatus {
  provider: string
  baseUrl: string
  status: string
  reachable: boolean
  checkedAt: string
  correlationId?: string
  latencyMs?: number
  httpStatus?: number
  error?: string
  probePath?: string
}

export interface ApiAuthDiagnostic {
  summary: string
  hints: string[]
  responseUrl?: string
}

export class ApiRequestError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
  }
}

export class ApiAuthDiagnosticError extends ApiRequestError {
  diagnostic: ApiAuthDiagnostic

  constructor(message: string, status: number, diagnostic: ApiAuthDiagnostic) {
    super(message, status)
    this.name = 'ApiAuthDiagnosticError'
    this.diagnostic = diagnostic
  }
}

export function isApiRequestError(error: unknown): error is ApiRequestError {
  return error instanceof ApiRequestError
}

export function isApiAuthDiagnosticError(error: unknown): error is ApiAuthDiagnosticError {
  return error instanceof ApiAuthDiagnosticError
}

export interface Project {
  id: string
  name: string
  environment: string
  health?: string
  sync?: string
  publicUrl?: string
  internalUrl?: string
  lastDeployAt?: string
}

export interface ProjectsResponse {
  projects: Project[]
}

export interface LoginPayload {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type?: string
  expires_at?: string
}

export interface ServiceEndpoint {
  type?: 'public' | 'internal' | string
  label?: string
  url: string
}

export interface ServiceDeployment {
  id: string
  version?: string
  status?: string
  deployedAt?: string
}

export interface ServiceDetails {
  id: string
  name: string
  namespace?: string
  env?: string
  appLabel?: string
  argoAppName?: string
  identity?: Partial<ServiceIdentity>
  version?: string
  health?: string
  sync?: string
  publicUrl?: string
  internalUrls?: string[]
  endpoints?: ServiceEndpoint[]
  deployments?: ServiceDeployment[]
}

export interface ServiceRegistryApiRow {
  serviceId: string
  serviceName: string
  env: string
  namespace: string
  appLabel: string
  argoAppName?: string
  source: string
  sourceRef?: string
  lastSyncedAt?: string
}

export interface ServicesResponse {
  services: ServiceRegistryApiRow[]
}

export interface CatalogJoinServiceRef {
  serviceId: string
  serviceName: string
  namespace: string
  appLabel: string
  argoAppName?: string
}

export interface CatalogJoinRow {
  projectId: string
  projectName: string
  env: string
  namespace: string
  appLabel: string
  joinSource: 'primary_key' | 'fallback_service_id' | 'unmatched' | string
  primaryServiceId?: string
  serviceCount: number
  serviceIds: string[]
  services: CatalogJoinServiceRef[]
}

export interface CatalogJoinDiagnostics {
  projectOnlyCount: number
  serviceOnlyCount: number
  oneToManyCount: number
  ambiguousJoinCount: number
  projectOnlyKeys: string[]
  serviceOnlyKeys: string[]
  oneToManyKeys: string[]
  ambiguousJoinKeys: string[]
}

export interface CatalogJoinResponse {
  generatedAt: string
  env?: string
  rows: CatalogJoinRow[]
  diagnostics: CatalogJoinDiagnostics
}

export interface RegistryFreshness {
  rowCount: number
  lastSyncedAt?: string
  staleAfterMinutes: number
  isEmpty: boolean
  isStale: boolean
  state: 'fresh' | 'stale' | 'empty' | string
}

export interface ProjectCatalogDiagnosticsResponse {
  generatedAt: string
  env?: string
  freshness: RegistryFreshness
  catalogJoin: CatalogJoinDiagnostics
}

export interface ServiceRegistryJoinMismatch {
  ciUnmatchedCount: number
  argoUnmatchedCount: number
  ciUnmatchedKeys: string[]
  argoUnmatchedKeys: string[]
}

export interface ServiceRegistryDiagnosticsResponse {
  generatedAt: string
  env?: string
  freshness: RegistryFreshness
  joinMismatch: ServiceRegistryJoinMismatch
  catalogJoin: CatalogJoinDiagnostics
}

export interface MonitoringProvidersDiagnosticsResponse {
  generatedAt: string
  overallStatus: string
  providers: MonitoringProviderStatus[]
}

async function getErrorMessage(response: Response) {
  const fallback = `Request failed (${response.status})`
  const contentType = response.headers.get('content-type') ?? ''

  if (!contentType.includes('application/json')) {
    return fallback
  }

  const payload = (await response.json()) as ApiErrorPayload
  if (typeof payload.detail === 'string') {
    return payload.detail
  }

  if (payload.detail && typeof payload.detail === 'object') {
    const message = payload.detail.message ?? payload.message ?? fallback
    const provider = payload.detail.providerStatus?.provider
    const providerState = payload.detail.providerStatus?.status
    const correlationId = payload.detail.correlationId
    const suffix = [provider, providerState, correlationId ? `correlationId=${correlationId}` : undefined]
      .filter(Boolean)
      .join(', ')
    return suffix ? `${message} (${suffix})` : message
  }

  return payload.message ?? fallback
}

function joinBaseUrl(baseUrl: string, path: string) {
  const normalizedBase = baseUrl.replace(/\/+$/, '')
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${normalizedBase}${normalizedPath}`
}

function normalizePreview(text: string) {
  return text.slice(0, 2500).toLowerCase()
}

function hasAuthMarkers(preview: string) {
  return (
    preview.includes('oauth2_proxy') ||
    preview.includes('sign in with github') ||
    preview.includes('/oauth2/start') ||
    preview.includes('secured with') ||
    preview.includes('<title>sign in')
  )
}

async function detectAuthDiagnostic(response: Response, requestPath: string) {
  const contentType = (response.headers.get('content-type') ?? '').toLowerCase()
  const isHtmlResponse = contentType.includes('text/html') || contentType.includes('application/xhtml+xml')
  const url = response.url || ''
  const normalizedUrl = url.toLowerCase()
  const looksLikeAuthUrl = normalizedUrl.includes('/oauth2/') || normalizedUrl.includes('/login')

  if (!isHtmlResponse && !looksLikeAuthUrl && !response.redirected) {
    return null
  }

  let preview = ''
  try {
    preview = normalizePreview(await response.clone().text())
  } catch {
    preview = ''
  }

  const looksLikeAuthHtml = isHtmlResponse && hasAuthMarkers(preview)
  const authStatus = response.status === 401 || response.status === 403

  if (!looksLikeAuthHtml && !looksLikeAuthUrl && !authStatus) {
    return null
  }

  return new ApiAuthDiagnosticError(
    `Authentication gateway blocked API request for ${requestPath}.`,
    response.status || 401,
    {
      summary: 'API returned an auth-gateway HTML/redirect response instead of JSON.',
      hints: [
        'Sign in again to refresh your portal session.',
        'Verify OAuth2-proxy forwards Authorization headers to the backend.',
        'Confirm /api routes are exempt from login-page HTML rewrites.',
      ],
      responseUrl: url || undefined,
    },
  )
}

export async function request<T>(path: string, options: RequestOptions = {}) {
  const { skipUnauthorizedRedirect = false, headers: inputHeaders, ...init } = options
  const headers = new Headers(inputHeaders)
  const token = getToken()

  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const url = joinBaseUrl(config.apiBaseUrl, path)
  const response = await fetch(url, {
    ...init,
    headers,
  })

  if (response.status === 401 && !skipUnauthorizedRedirect) {
    clearToken()
    window.dispatchEvent(
      new CustomEvent(UNAUTHORIZED_EVENT, {
        detail: { message: 'Your session expired. Please sign in again.' },
      }),
    )
  }

  const authDiagnostic = await detectAuthDiagnostic(response, path)
  if (authDiagnostic) {
    throw authDiagnostic
  }

  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

async function requestServiceEndpoint<T>(path: string) {
  if (serviceApiAvailability === 'unavailable') {
    throw new ApiRequestError('Service endpoint is not available in this backend.', 404)
  }

  try {
    const response = await request<T>(path)
    serviceApiAvailability = 'available'
    return response
  } catch (error) {
    if (isApiRequestError(error) && serviceEndpointMissingStatuses.has(error.status)) {
      serviceApiAvailability = 'unavailable'
    }
    throw error
  }
}

export function getProjects() {
  return request<ProjectsResponse>('/projects')
}

export function getProjectCatalogDiagnostics() {
  return request<ProjectCatalogDiagnosticsResponse>('/projects/diagnostics')
}

export function login(payload: LoginPayload) {
  return request<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
    skipUnauthorizedRedirect: true,
  })
}

export function getService(serviceId: string) {
  return requestServiceEndpoint<ServiceDetails>(`/services/${encodeURIComponent(serviceId)}`)
}

export function getServices() {
  return requestServiceEndpoint<ServicesResponse>('/services')
}

export function getCatalogReconciliation() {
  return request<CatalogJoinResponse>('/catalog/reconciliation')
}

export function getServiceRegistryDiagnostics() {
  return request<ServiceRegistryDiagnosticsResponse>('/service-registry/diagnostics')
}

export function getMonitoringProvidersDiagnostics() {
  return request<MonitoringProvidersDiagnosticsResponse>('/monitoring/providers/diagnostics')
}

export function getServiceDeployments(serviceId: string) {
  return requestServiceEndpoint<{ deployments: ServiceDeployment[] }>(
    `/services/${encodeURIComponent(serviceId)}/deployments`,
  )
}
