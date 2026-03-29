import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { PortalSidebar } from '../src/components/navigation/portal-sidebar.js'
import {
  primaryMobileNavLinks,
  primaryNavLinks,
} from '../src/components/navigation/primary-nav-links.js'
import { installMockBrowser, renderToHtml } from './test-setup.js'

test('primary desktop navigation no longer includes Projects', () => {
  const browser = installMockBrowser({ pathname: '/dashboard' })
  const markup = renderToHtml(
    createElement(PortalSidebar, {
      pathname: '/dashboard',
      theme: 'light',
      onThemeToggle() {},
    }),
  )

  assert.deepEqual(
    primaryNavLinks.map((link) => link.label),
    ['Dashboard', 'Services', 'Platform Health', 'Settings'],
  )
  assert.match(markup, /Dashboard/)
  assert.match(markup, /Services/)
  assert.match(markup, /Platform Health/)
  assert.match(markup, /Settings/)
  assert.doesNotMatch(markup, /Projects/)
  browser.cleanup()
})

test('primary mobile navigation no longer includes Projects', () => {
  assert.deepEqual(
    primaryMobileNavLinks.map((link) => link.label),
    ['Dashboard', 'Services', 'Platform Health', 'Settings'],
  )
  assert.equal(primaryMobileNavLinks.some((link) => link.to === '/projects'), false)
})
