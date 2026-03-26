import { clearToken, getToken } from '@/lib/auth'
import { config } from '@/lib/config'
import { dispatchUnauthorized } from '@/lib/http/auth-events'
import { ApiRequestError, detectApiAuthDiagnostic, getApiErrorMessage } from '@/lib/http/errors'

export interface RequestOptions extends Omit<RequestInit, 'headers'> {
  headers?: HeadersInit
  skipUnauthorizedRedirect?: boolean
}

function joinBaseUrl(baseUrl: string, path: string) {
  const normalizedBase = baseUrl.replace(/\/+$/, '')
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${normalizedBase}${normalizedPath}`
}

export async function request<T>(path: string, options: RequestOptions = {}) {
  // Centralize auth headers, JSON defaults, and API-level error translation so
  // adapters and domain modules can focus on request intent instead of fetch ceremony.
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
    dispatchUnauthorized()
  }

  const authDiagnostic = await detectApiAuthDiagnostic(response, path)
  if (authDiagnostic) {
    throw authDiagnostic
  }

  if (!response.ok) {
    throw new ApiRequestError(await getApiErrorMessage(response), response.status)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}
