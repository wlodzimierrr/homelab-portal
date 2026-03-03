import { clearToken, getToken } from '@/lib/auth'
import { config } from '@/lib/config'

export const UNAUTHORIZED_EVENT = 'portal:unauthorized'

interface RequestOptions extends Omit<RequestInit, 'headers'> {
  headers?: HeadersInit
  skipUnauthorizedRedirect?: boolean
}

interface ApiErrorPayload {
  detail?: string
  message?: string
}

export interface Project {
  id: string
  name: string
  environment: string
  health?: string
  sync?: string
  publicUrl?: string
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

  if (!response.ok) {
    throw new Error(await getErrorMessage(response))
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
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
