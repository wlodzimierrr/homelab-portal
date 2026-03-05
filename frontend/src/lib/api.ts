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
  detail?: string
  message?: string
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

export interface CreateProjectPayload {
  id: string
  name: string
  environment: string
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

async function getErrorMessage(response: Response) {
  const fallback = `Request failed (${response.status})`
  const contentType = response.headers.get('content-type') ?? ''

  if (!contentType.includes('application/json')) {
    return fallback
  }

  const payload = (await response.json()) as ApiErrorPayload
  return payload.detail ?? payload.message ?? fallback
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

export function createProject(payload: CreateProjectPayload) {
  return request<Project>('/projects', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
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

export function getServiceDeployments(serviceId: string) {
  return requestServiceEndpoint<{ deployments: ServiceDeployment[] }>(
    `/services/${encodeURIComponent(serviceId)}/deployments`,
  )
}
