import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { ServiceDetailsPage } from '../src/features/service-details/page.js'
import { installTestWindow, render } from './test-setup.js'

test('ServiceDetailsPage no longer renders settings-owned section titles in its initial overview render', () => {
  installTestWindow('/services/homelab-api')
  const markup = render(React.createElement(ServiceDetailsPage, { serviceId: 'homelab-api' }))

  assert.doesNotMatch(markup, /Public hostname/)
  assert.doesNotMatch(markup, /Runtime Config/)
  assert.doesNotMatch(markup, /Adopt into Project/)
})

test('ServiceDetailsPage links to service settings from overview', () => {
  installTestWindow('/services/homelab-api')
  const markup = render(React.createElement(ServiceDetailsPage, { serviceId: 'homelab-api' }))

  assert.match(markup, /Service settings/)
})
