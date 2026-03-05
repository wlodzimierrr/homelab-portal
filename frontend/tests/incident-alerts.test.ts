import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import {
  buildIncidentAlertSnapshot,
  normalizeIncidentSeverityThreshold,
  shouldShowIncidentBanner,
} from '../src/lib/incident-alerts.js'

describe('buildIncidentAlertSnapshot', () => {
  it('counts only active incidents and aggregates service alert severities', () => {
    const snapshot = buildIncidentAlertSnapshot([
      { severity: 'warning', status: 'active', serviceId: 'svc-a' },
      { severity: 'critical', status: 'active', serviceId: 'svc-a' },
      { severity: 'info', status: 'resolved', serviceId: 'svc-b' },
      { severity: 'info', status: 'active' },
    ])

    assert.equal(snapshot.activeCount, 3)
    assert.equal(snapshot.highestSeverity, 'critical')
    assert.equal(snapshot.serviceAlerts['svc-a'].total, 2)
    assert.equal(snapshot.serviceAlerts['svc-a'].highestSeverity, 'critical')
  })
})

describe('normalizeIncidentSeverityThreshold', () => {
  it('falls back to warning for invalid input', () => {
    assert.equal(normalizeIncidentSeverityThreshold(undefined), 'warning')
    assert.equal(normalizeIncidentSeverityThreshold('invalid'), 'warning')
  })
})

describe('shouldShowIncidentBanner', () => {
  it('keeps critical incidents visible even when dismissed', () => {
    const show = shouldShowIncidentBanner(
      { activeCount: 1, highestSeverity: 'critical', serviceAlerts: {} },
      { threshold: 'warning', dismissed: true },
    )
    assert.equal(show, true)
  })

  it('hides warning incidents when dismissed for session', () => {
    const show = shouldShowIncidentBanner(
      { activeCount: 1, highestSeverity: 'warning', serviceAlerts: {} },
      { threshold: 'warning', dismissed: true },
    )
    assert.equal(show, false)
  })
})
