import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { createServiceIdentity } from '../src/lib/service-identity.js'
import { DeploymentsTable } from '../src/features/service-deployments/components/deployments-table.js'
import { installTestWindow, render } from './test-setup.js'

test('DeploymentsTable still renders deployment history rows', () => {
  installTestWindow('/services/homelab-api/deployments')
  const deployment = {
    id: 'dep-1',
    identity: createServiceIdentity({ serviceId: 'homelab-api', env: 'prod' }),
    action: 'rollback',
    version: 'sha-prod-2',
    outcome: 'live',
    deployedAt: '2026-03-28T12:00:00Z',
    deployReason: 'restore known good version',
    errorRatePct: {},
    p95LatencyMs: {},
    availabilityPct: {},
    hasComparisonWindow: false,
    regressionScore: 0,
    evidenceSource: 'deployment_record' as const,
    metricsSource: 'none' as const,
  }
  const markup = render(
    React.createElement(DeploymentsTable, {
      deployments: [deployment],
      isLoading: false,
      error: '',
      loadDeployments: async () => {},
      actionFilter: 'all',
      setActionFilter() {},
      availableActions: ['rollback'],
      statusFilter: 'all',
      setStatusFilter() {},
      availableStatuses: ['live'],
      impactFilterMode: 'all',
      setImpactFilterMode() {},
      sortMode: 'newest',
      setSortMode() {},
      visibleDeployments: [deployment],
      hasAnyComparisonWindow: false,
      selectedDeployment: deployment,
      setSelectedDeploymentId() {},
    }),
  )

  assert.match(markup, /Rollback/)
  assert.match(markup, /sha-prod-2/)
  assert.match(markup, /Inspecting deploy window/)
})
