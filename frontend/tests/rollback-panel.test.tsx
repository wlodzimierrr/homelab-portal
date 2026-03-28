import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { createServiceIdentity } from '../src/lib/service-identity.js'
import { RollbackPanel } from '../src/features/service-deployments/components/rollback-panel.js'
import { installMockBrowser, renderToHtml } from './test-setup.js'

test('RollbackPanel renders rollback controls on the deployments side', () => {
  const browser = installMockBrowser({ pathname: '/services/homelab-api/deployments' })
  const markup = renderToHtml(
    createElement(RollbackPanel, {
      rollbackSupported: true,
      rollbackEnvs: ['dev', 'prod'],
      deploymentHistory: [
        {
          id: 'dep-1',
          identity: createServiceIdentity({ serviceId: 'homelab-api', env: 'dev' }),
          action: 'deploy',
          version: 'sha-dev-1',
          outcome: 'live',
          errorRatePct: {},
          p95LatencyMs: {},
          availabilityPct: {},
          hasComparisonWindow: false,
          regressionScore: 0,
          evidenceSource: 'deployment_record',
          metricsSource: 'none',
        },
      ],
      rollbackTargetEnvironment: 'dev',
      setRollbackTargetEnvironment() {},
      rollbackCandidates: {
        serviceId: 'homelab-api',
        targetEnvironment: 'dev',
        currentTag: 'sha-dev-2',
        candidates: [
          {
            tag: 'sha-dev-1',
            imageRef: 'ghcr.io/homelab/api:sha-dev-1',
            publishedAt: '2026-03-28T12:00:00Z',
          },
        ],
        generatedAt: '2026-03-28T12:05:00Z',
      },
      rollbackCandidatesLoading: false,
      rollbackCandidatesError: '',
      selectedRollbackTag: 'sha-dev-1',
      setSelectedRollbackTag() {},
      rollbackReason: '',
      setRollbackReason() {},
      rollbackSubmitting: false,
      rollbackError: '',
      rollbackResult: null,
      rollbackLockActive: false,
      rollbackInFlight: false,
      onSubmitRollback() {},
    }),
  )

  assert.match(markup, /Portal Rollback/)
  assert.match(markup, /Request dev rollback/)
  browser.cleanup()
})
