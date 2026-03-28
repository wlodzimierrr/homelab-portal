import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { OverviewMetricsSummary } from '../src/features/service-details/components/overview-metrics-summary.js'
import { installMockBrowser, renderToHtml } from './test-setup.js'

test('OverviewMetricsSummary renders the compact service metrics panel', () => {
  const browser = installMockBrowser({ pathname: '/services/homelab-api' })
  const markup = renderToHtml(
    createElement(OverviewMetricsSummary, {
      health: 'healthy',
      metrics: {
        serviceId: 'homelab-api',
        range: '24h',
        p95LatencyMs: 187,
        errorRatePct: 0.12,
        generatedAt: '2026-03-28T12:00:00Z',
        noData: {
          uptimePct: false,
          p95LatencyMs: false,
          errorRatePct: false,
          restartCount: false,
        },
        observabilityDiagnostics: {
          missingMetrics: [],
        },
      },
      isLoading: false,
      error: '',
      coverageMessage: '',
      onRetry() {},
    }),
  )

  assert.match(markup, /Overview Metrics/)
  assert.match(markup, /Healthy/)
  assert.match(markup, /Latency/)
  assert.match(markup, /187 ms/)
  assert.match(markup, /Error Rate/)
  assert.match(markup, /0\.12%/)
  assert.match(markup, /Last Refresh/)
  assert.doesNotMatch(markup, /Logs Console/)
  assert.doesNotMatch(markup, /Latency &amp; Error Trends/)
  assert.doesNotMatch(markup, /Service Health Timeline/)
  browser.cleanup()
})
