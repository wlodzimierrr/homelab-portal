import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { ServiceGroupingContext } from '../src/features/service-details/components/service-grouping-context.js'
import { installMockBrowser, renderToHtml } from './test-setup.js'

test('ServiceGroupingContext renders project name, namespace, and related services', () => {
  const browser = installMockBrowser({ pathname: '/services/homelab-api' })
  const markup = renderToHtml(
    createElement(ServiceGroupingContext, {
      projectContext: {
        projectId: 'homelab',
        projectName: 'Homelab',
        namespace: 'homelab-prod',
        siblingServiceIds: ['homelab-web', 'homelab-worker'],
        isLinked: true,
      },
      serviceEnv: 'prod',
    }),
  )

  assert.match(markup, /Service Group/)
  assert.match(markup, /Linked project/)
  assert.match(markup, /Homelab/)
  assert.match(markup, /Namespace/)
  assert.match(markup, /homelab-prod/)
  assert.match(markup, /Related services/)
  assert.match(markup, /homelab-web/)
  assert.match(markup, /homelab-worker/)
  assert.match(markup, /View project diagnostics/)
  assert.doesNotMatch(markup, />Homelab<\/a>/)
  assert.doesNotMatch(markup, /Sibling services:/)
  browser.cleanup()
})

test('ServiceGroupingContext omits the diagnostics link when projectId is absent', () => {
  const browser = installMockBrowser({ pathname: '/services/homelab-api' })
  const markup = renderToHtml(
    createElement(ServiceGroupingContext, {
      projectContext: {
        projectId: null,
        projectName: 'Homelab',
        namespace: 'homelab-prod',
        siblingServiceIds: [],
        isLinked: true,
      },
      serviceEnv: 'prod',
    }),
  )

  assert.match(markup, /Homelab/)
  assert.match(markup, /homelab-prod/)
  assert.doesNotMatch(markup, /View project diagnostics/)
  browser.cleanup()
})
