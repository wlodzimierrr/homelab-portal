import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { ForwardActionsPanel } from '../src/features/service-details/components/forward-actions-panel.js'
import { installTestWindow, render } from './test-setup.js'

test('ForwardActionsPanel renders deploy and promote controls without rollback', () => {
  installTestWindow('/services/homelab-api')
  const markup = render(
    React.createElement(ForwardActionsPanel, {
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
})
