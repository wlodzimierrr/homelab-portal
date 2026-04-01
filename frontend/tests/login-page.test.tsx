import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { LoginPage } from '../src/pages/login-page.js'
import { installMockBrowser, renderToHtml } from './test-setup.js'

test('LoginPage renders manual token login in bearer_token mode', () => {
  const browser = installMockBrowser({ pathname: '/login', token: null, authMode: 'bearer_token' })

  const html = renderToHtml(createElement(LoginPage, { onLoginSuccess: () => undefined }))

  assert.match(html, /Portal Login/)
  assert.match(html, /Username/)
  assert.match(html, /Password/)
  assert.match(html, /Sign in/)
  assert.doesNotMatch(html, /Manual portal token login is disabled here/)

  browser.cleanup()
})

test('LoginPage disables manual token login in forwarded_identity mode', () => {
  const browser = installMockBrowser({ pathname: '/login', token: null, authMode: 'forwarded_identity' })

  const html = renderToHtml(createElement(LoginPage, { onLoginSuccess: () => undefined }))

  assert.match(html, /Portal Sign-In/)
  assert.match(html, /authoritative identity source/)
  assert.match(html, /Manual portal token login is disabled here/)
  assert.doesNotMatch(html, /Username/)
  assert.doesNotMatch(html, /Password/)

  browser.cleanup()
})
