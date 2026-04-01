import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { DecommissionServiceSection } from '../src/features/service-settings/components/decommission-service-section.js'
import { installMockBrowser, renderToHtml } from './test-setup.js'

test('DecommissionServiceSection requires exact service-id confirmation before enabling the action', () => {
  const browser = installMockBrowser({ pathname: '/services/demo/settings' })

  const mismatchMarkup = renderToHtml(
    createElement(DecommissionServiceSection, {
      serviceId: 'demo',
      mode: 'standalone',
      reason: null,
      confirmationValue: 'demo-typo',
      setConfirmationValue() {},
      submitting: false,
      error: '',
      result: null,
      onSubmit() {},
    }),
  )

  const matchingMarkup = renderToHtml(
    createElement(DecommissionServiceSection, {
      serviceId: 'demo',
      mode: 'standalone',
      reason: null,
      confirmationValue: 'demo',
      setConfirmationValue() {},
      submitting: false,
      error: '',
      result: null,
      onSubmit() {},
    }),
  )

  assert.match(mismatchMarkup, /Type <code>demo<\/code> to confirm decommissioning/)
  assert.match(mismatchMarkup, /Decommission service/)
  assert.match(mismatchMarkup, /<button[^>]* disabled=/)
  assert.doesNotMatch(matchingMarkup, /Decommission not available/)
  assert.doesNotMatch(matchingMarkup, /<button[^>]* disabled=/)

  browser.cleanup()
})

test('DecommissionServiceSection tolerates invisible whitespace around the service id', () => {
  const browser = installMockBrowser({ pathname: '/services/my-service/settings' })

  const markup = renderToHtml(
    createElement(DecommissionServiceSection, {
      serviceId: 'my-service ',
      mode: 'standalone',
      reason: null,
      confirmationValue: 'my-service',
      setConfirmationValue() {},
      submitting: false,
      error: '',
      result: null,
      onSubmit() {},
    }),
  )

  assert.doesNotMatch(markup, /<button[^>]* disabled=/)

  browser.cleanup()
})

test('DecommissionServiceSection shows a safe informational state for ineligible project-linked services', () => {
  const browser = installMockBrowser({ pathname: '/services/oauth2-proxy/settings' })
  const markup = renderToHtml(
    createElement(DecommissionServiceSection, {
      serviceId: 'oauth2-proxy',
      mode: 'unsupported',
      reason: 'Legacy or manually managed shared services cannot be decommissioned from Service Settings yet.',
      confirmationValue: '',
      setConfirmationValue() {},
      submitting: false,
      error: '',
      result: null,
      onSubmit() {},
    }),
  )

  assert.match(markup, /manually managed shared services/)
  assert.match(markup, /Decommission not available/)
  assert.match(markup, /<button[^>]* disabled=/)

  browser.cleanup()
})

test('DecommissionServiceSection shows PR result details after a successful request', () => {
  const browser = installMockBrowser({ pathname: '/services/demo/settings' })
  const markup = renderToHtml(
    createElement(DecommissionServiceSection, {
      serviceId: 'demo',
      mode: 'standalone',
      reason: null,
      confirmationValue: 'demo',
      setConfirmationValue() {},
      submitting: false,
      error: '',
      result: {
        status: 'accepted',
        serviceId: 'demo',
        projectId: null,
        requestedBy: 'alice',
        repository: 'wlodzimierrr/homelab-workloads',
        baseBranch: 'main',
        branchName: 'decommission/demo-123',
        prUrl: 'https://github.com/example/homelab-workloads/pull/42',
        prNumber: 42,
        updatedPaths: ['services.yaml'],
        removedPaths: ['apps/demo/base/deployment.yaml'],
        preservedArtifacts: ['source-repository', 'ghcr-package'],
        message: 'PR created. Workloads and catalog state will be removed after merge.',
        initiatedAt: '2026-03-31T10:00:00Z',
      },
      onSubmit() {},
    }),
  )

  assert.match(markup, /Decommission PR created/)
  assert.match(markup, /View decommission PR/)
  assert.match(markup, /Preserved in v1: source-repository, ghcr-package\./)

  browser.cleanup()
})

test('DecommissionServiceSection uses project-removal wording for project components', () => {
  const browser = installMockBrowser({ pathname: '/services/my-proj-worker/settings' })
  const markup = renderToHtml(
    createElement(DecommissionServiceSection, {
      serviceId: 'my-proj-worker',
      mode: 'project-component',
      reason:
        'This will remove only this service from the shared project while preserving the project and sibling services.',
      confirmationValue: 'my-proj-worker',
      setConfirmationValue() {},
      submitting: false,
      error: '',
      result: null,
      onSubmit() {},
    }),
  )

  assert.match(markup, /removing this service from the project/i)
  assert.match(markup, /shared project, namespace, sibling services, source repo, and GHCR artifacts stay in place/i)
  assert.match(markup, /Remove service from project/)

  browser.cleanup()
})
