import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { ServiceDetailsPage } from '../src/features/service-details/page.js'
import { installMockBrowser, renderToHtml } from './test-setup.js'

test('ServiceDetailsPage no longer renders settings-owned section titles in its initial overview render', () => {
  const browser = installMockBrowser({ pathname: '/services/homelab-api' })
  const markup = renderToHtml(createElement(ServiceDetailsPage, { serviceId: 'homelab-api' }))

  assert.doesNotMatch(markup, /Public hostname/)
  assert.doesNotMatch(markup, /Runtime Config/)
  assert.doesNotMatch(markup, /Adopt into Project/)
  browser.cleanup()
})

test('ServiceDetailsPage links to service settings from overview', () => {
  const browser = installMockBrowser({ pathname: '/services/homelab-api' })
  const markup = renderToHtml(createElement(ServiceDetailsPage, { serviceId: 'homelab-api' }))

  assert.match(markup, /Service settings/)
  browser.cleanup()
})

test('ServiceDetailsPage links to deployment history and rollback from overview', () => {
  const browser = installMockBrowser({ pathname: '/services/homelab-api' })
  const markup = renderToHtml(createElement(ServiceDetailsPage, { serviceId: 'homelab-api' }))

  assert.match(markup, /View deployment history &amp; rollback/)
  assert.doesNotMatch(markup, /Portal Rollback/)
  assert.doesNotMatch(markup, /Latency &amp; Error Trends/)
  assert.doesNotMatch(markup, /Logs Console/)
  assert.doesNotMatch(markup, /Logs Quick View/)
  assert.doesNotMatch(markup, /Service Health Timeline/)
  browser.cleanup()
})
