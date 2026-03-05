import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import {
  DEFAULT_DEPLOYMENT_ALERT_THRESHOLDS,
  evaluateDeploymentAlert,
  summarizeDeploymentAlerts,
} from '../src/lib/deployment-alerts.js'

describe('evaluateDeploymentAlert', () => {
  it('returns none for stable deployment metrics', () => {
    const result = evaluateDeploymentAlert({
      outcome: 'succeeded',
      regressionScore: 0.1,
      errorRateDeltaPct: 0.05,
      latencyDeltaMs: 20,
      availabilityDeltaPct: -0.02,
    })

    assert.equal(result.level, 'none')
    assert.equal(result.suspicious, false)
  })

  it('flags warning based on configurable thresholds', () => {
    const result = evaluateDeploymentAlert({
      outcome: 'succeeded',
      regressionScore: DEFAULT_DEPLOYMENT_ALERT_THRESHOLDS.warningRegressionScore,
    })

    assert.equal(result.level, 'warning')
    assert.equal(result.suspicious, true)
  })

  it('flags critical for suspicious outcomes', () => {
    const result = evaluateDeploymentAlert({ outcome: 'failed' })
    assert.equal(result.level, 'critical')
    assert.equal(result.suspicious, true)
    assert.ok(result.reasons.some((reason) => reason.includes('outcome=failed')))
  })
})

describe('summarizeDeploymentAlerts', () => {
  it('returns highest severity across deployments', () => {
    const summary = summarizeDeploymentAlerts([
      { outcome: 'succeeded', regressionScore: 0.2 },
      { outcome: 'succeeded', regressionScore: 0.9 },
      { outcome: 'degraded', regressionScore: 0.1 },
    ])

    assert.equal(summary.level, 'critical')
    assert.equal(summary.suspicious, true)
  })
})
