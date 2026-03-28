import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { OverviewObservabilityLinks } from '../src/features/service-details/components/overview-observability-links.js'
import { installMockBrowser, renderToHtml } from './test-setup.js'

test('OverviewObservabilityLinks keeps external observability destinations visible', () => {
  const browser = installMockBrowser({ pathname: '/services/homelab-api' })
  const markup = renderToHtml(
    createElement(OverviewObservabilityLinks, {
      argoUrl: 'https://argo.example/apps/homelab-api',
      grafanaDashboardLink: { href: 'https://grafana.example/d/service' },
      latencyPanelLink: { href: 'https://grafana.example/d/service?view=latency' },
      errorPanelLink: { href: 'https://grafana.example/d/service?view=errors' },
      logsLink: { href: 'https://grafana.example/explore?query=loki' },
    }),
  )

  assert.match(markup, /External Tools/)
  assert.match(markup, /Argo CD Application/)
  assert.match(markup, /Grafana Dashboard/)
  assert.match(markup, /Latency Panel/)
  assert.match(markup, /Error Panel/)
  assert.match(markup, /Logs/)
  assert.match(markup, /https:\/\/argo\.example\/apps\/homelab-api/)
  assert.match(markup, /https:\/\/grafana\.example\/d\/service/)
  assert.match(markup, /https:\/\/grafana\.example\/explore\?query=loki/)
  browser.cleanup()
})
