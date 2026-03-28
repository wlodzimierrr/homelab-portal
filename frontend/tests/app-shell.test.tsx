import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement, type ComponentType } from 'react'
import { installMockBrowser, renderToHtml } from './test-setup.js'

async function loadAppShell() {
  const module = await import('../src/app/AppShell.js')
  return module.default as ComponentType
}

test('AppShell renders the current service overview route', async () => {
  const browser = installMockBrowser({ pathname: '/services/api-gateway' })
  const AppShell = await loadAppShell()
  const html = renderToHtml(createElement(AppShell))

  assert.match(html, /Service:\s*api-gateway/)
  assert.match(html, /View deployment history &amp; rollback/)
  assert.doesNotMatch(html, /Back to overview/)
  browser.cleanup()
})

test('AppShell renders the service settings route', async () => {
  const browser = installMockBrowser({ pathname: '/services/api-gateway/settings' })
  const AppShell = await loadAppShell()
  const html = renderToHtml(createElement(AppShell))

  assert.match(html, /Settings:\s*api-gateway/)
  assert.match(html, /Back to overview/)
  browser.cleanup()
})

test('AppShell renders the current service deployments route', async () => {
  const browser = installMockBrowser({ pathname: '/services/api-gateway/deployments' })
  const AppShell = await loadAppShell()
  const html = renderToHtml(createElement(AppShell))

  assert.match(html, /Deployments:\s*api-gateway/)
  assert.match(html, /Back to overview/)
  assert.doesNotMatch(html, /View deployments/)
  browser.cleanup()
})
