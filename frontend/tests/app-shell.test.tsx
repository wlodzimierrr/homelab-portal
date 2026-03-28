import assert from 'node:assert/strict'
import { after, before, describe, it } from 'node:test'
import { createElement, type ComponentType } from 'react'
import { installMockBrowser, renderToHtml } from './test-setup.js'

let AppShell: ComponentType
let browser: ReturnType<typeof installMockBrowser>

describe('AppShell service route rendering', () => {
  before(async () => {
    browser = installMockBrowser({ pathname: '/services/api-gateway' })
    ;({ default: AppShell } = await import('../src/app/AppShell.js'))
  })

  after(() => {
    browser.cleanup()
  })

  it('renders the current service overview route', () => {
    browser.window.location.pathname = '/services/api-gateway'

    const html = renderToHtml(createElement(AppShell))

    assert.match(html, /Service:\s*api-gateway/)
    assert.match(html, /View deployments/)
    assert.doesNotMatch(html, /Back to overview/)
  })

  it('renders the current service deployments route', () => {
    browser.window.location.pathname = '/services/api-gateway/deployments'

    const html = renderToHtml(createElement(AppShell))

    assert.match(html, /Deployments:\s*api-gateway/)
    assert.match(html, /Back to overview/)
    assert.doesNotMatch(html, /View deployments/)
  })
})
