import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { ForwardActionsPanel } from '../src/features/service-details/components/forward-actions-panel.js'
import { installMockBrowser, renderToHtml } from './test-setup.js'

test('ForwardActionsPanel renders deploy and promote controls without rollback', () => {
  const browser = installMockBrowser({ pathname: '/services/homelab-api' })
  const markup = renderToHtml(
    createElement(ForwardActionsPanel, {
      serviceId: 'homelab-api',
      deploySupported: true,
      promoteSupported: true,
      latestDevDeployment: undefined,
      latestProdDeployment: undefined,
      recentDevTags: ['sha-dev-1'],
      recentProdTags: ['sha-prod-1'],
      devLockActive: false,
      prodLockActive: false,
      devInFlight: false,
      prodInFlight: false,
      deployReason: '',
      setDeployReason() {},
      deploySubmitting: false,
      deployError: '',
      deployResult: null,
      onSubmitDeploy() {},
      promoteReason: '',
      setPromoteReason() {},
      promoteSubmitting: false,
      promoteError: '',
      promoteResult: null,
      onSubmitPromote() {},
    }),
  )

  assert.match(markup, /Deploy latest to dev/)
  assert.match(markup, /Promote dev to prod/)
  assert.doesNotMatch(markup, /Portal Rollback/)
  browser.cleanup()
})
