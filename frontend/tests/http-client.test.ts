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
