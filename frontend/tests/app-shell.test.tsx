import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { installTestWindow, render } from './test-setup.js'

test('AppShell renders the service settings route', async () => {
  installTestWindow('/services/homelab-api/settings')
  const { default: AppShell } = await import('../src/app/AppShell.js')
  const markup = render(React.createElement(AppShell))

  assert.match(markup, /Settings: homelab-api/)
})

test('AppShell still renders the service overview route', async () => {
  installTestWindow('/services/homelab-api')
  const { default: AppShell } = await import('../src/app/AppShell.js')
  const markup = render(React.createElement(AppShell))

  assert.match(markup, /Service: homelab-api/)
})

test('AppShell still renders the service deployments route', async () => {
  installTestWindow('/services/homelab-api/deployments')
  const { default: AppShell } = await import('../src/app/AppShell.js')
  const markup = render(React.createElement(AppShell))

  assert.match(markup, /Deployments: homelab-api/)
})
