export interface ApiAuthDiagnostic {
  summary: string
  hints: string[]
  responseUrl?: string
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

export async function getApiErrorMessage(response: Response) {
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

export async function detectApiAuthDiagnostic(response: Response, requestPath: string) {
  // In production the frontend may sit behind an auth gateway. If an API call
  // gets redirected to login HTML, surface a tailored error instead of a generic
  // JSON parse failure so the UI can explain what actually happened.
  const contentType = (response.headers.get('content-type') ?? '').toLowerCase()
  const isHtmlResponse = contentType.includes('text/html') || contentType.includes('application/xhtml+xml')
  const url = response.url || ''
  const normalizedUrl = url.toLowerCase()
  const normalizedRequestPath = requestPath.toLowerCase()
  const isLoginApiRequest = normalizedRequestPath === '/auth/login'
  const looksLikeAuthUrl =
    normalizedUrl.includes('/oauth2/') || (!isLoginApiRequest && normalizedUrl.includes('/login'))

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
