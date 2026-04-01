import assert from 'node:assert/strict'
import test from 'node:test'
import { UNAUTHORIZED_EVENT } from '../src/lib/http/auth-events.js'
import { request } from '../src/lib/http/client.js'
import { ApiAuthDiagnosticError, ApiRequestError } from '../src/lib/http/errors.js'
import { installMockBrowser } from './test-setup.js'

test('request does not clear the auth token when a gateway-style HTML 401 blocks an API call', async () => {
  const browser = installMockBrowser({ pathname: '/services/homelab-wordpress/settings', token: 'keep-me' })
  let unauthorizedEvents = 0
  window.addEventListener(UNAUTHORIZED_EVENT, () => {
    unauthorizedEvents += 1
  })

  const previousFetch = globalThis.fetch
  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: async () =>
      new Response('<html><title>Sign in</title>Sign in with GitHub</html>', {
        status: 401,
        headers: {
          'content-type': 'text/html',
        },
      }),
  })

  await assert.rejects(
    () => request('/services/homelab-wordpress/decommission', { method: 'POST' }),
    (error: unknown) => error instanceof ApiAuthDiagnosticError,
  )

  assert.equal(window.localStorage.getItem('portal-auth-token'), 'keep-me')
  assert.equal(unauthorizedEvents, 0)

  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: previousFetch,
  })
  browser.cleanup()
})

test('request still clears the auth token for a real JSON 401 from the API', async () => {
  const browser = installMockBrowser({ pathname: '/services/demo/settings', token: 'expire-me' })
  let unauthorizedEvents = 0
  window.addEventListener(UNAUTHORIZED_EVENT, () => {
    unauthorizedEvents += 1
  })

  const previousFetch = globalThis.fetch
  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: async () =>
      new Response(JSON.stringify({ detail: 'Missing bearer token' }), {
        status: 401,
        headers: {
          'content-type': 'application/json',
        },
      }),
  })

  await assert.rejects(
    () => request('/services/demo/decommission', { method: 'POST' }),
    (error: unknown) => error instanceof ApiRequestError && error.status === 401,
  )

  assert.equal(window.localStorage.getItem('portal-auth-token'), null)
  assert.equal(unauthorizedEvents, 1)

  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: previousFetch,
  })
  browser.cleanup()
})

test('request includes the bearer token header in bearer_token mode', async () => {
  const browser = installMockBrowser({ pathname: '/dashboard', token: 'dev-static-token', authMode: 'bearer_token' })
  let authorizationHeader: string | null = null

  const previousFetch = globalThis.fetch
  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: async (_input: unknown, init?: RequestInit) => {
      const headers = new Headers(init?.headers)
      authorizationHeader = headers.get('Authorization')
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: {
          'content-type': 'application/json',
        },
      })
    },
  })

  await request('/health')

  assert.equal(authorizationHeader, 'Bearer dev-static-token')

  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: previousFetch,
  })
  browser.cleanup()
})

test('request omits the bearer token header in forwarded_identity mode', async () => {
  const browser = installMockBrowser({
    pathname: '/dashboard',
    token: 'dev-static-token',
    authMode: 'forwarded_identity',
  })
  let authorizationHeader: string | null = null

  const previousFetch = globalThis.fetch
  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: async (_input: unknown, init?: RequestInit) => {
      const headers = new Headers(init?.headers)
      authorizationHeader = headers.get('Authorization')
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: {
          'content-type': 'application/json',
        },
      })
    },
  })

  await request('/health')

  assert.equal(authorizationHeader, null)

  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: previousFetch,
  })
  browser.cleanup()
})
