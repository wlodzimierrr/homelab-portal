import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import {
  DEFAULT_UPTIME_THRESHOLD_CONFIG,
  classifyUptime,
  isMetricStale,
  mergeUptimeSeverities,
} from '../src/lib/uptime-status.js'

describe('classifyUptime', () => {
  it('returns unknown for undefined', () => {
    assert.equal(classifyUptime(undefined), 'unknown')
  })

  it('maps healthy/warning/critical based on centralized thresholds', () => {
    assert.equal(classifyUptime(DEFAULT_UPTIME_THRESHOLD_CONFIG.healthyMin), 'healthy')
    assert.equal(classifyUptime(DEFAULT_UPTIME_THRESHOLD_CONFIG.warningMin), 'warning')
    assert.equal(classifyUptime(DEFAULT_UPTIME_THRESHOLD_CONFIG.warningMin - 0.01), 'critical')
  })
})

describe('isMetricStale', () => {
  it('returns false when no timestamp is provided', () => {
    assert.equal(isMetricStale(undefined), false)
  })

  it('returns true when timestamp is older than stale threshold', () => {
    const staleDate = new Date(Date.now() - (DEFAULT_UPTIME_THRESHOLD_CONFIG.staleAfterMinutes + 1) * 60_000)
    assert.equal(isMetricStale(staleDate.toISOString()), true)
  })

  it('returns false when timestamp is recent', () => {
    const freshDate = new Date(Date.now() - 2 * 60_000)
    assert.equal(isMetricStale(freshDate.toISOString()), false)
  })
})

describe('mergeUptimeSeverities', () => {
  it('returns worst severity across values', () => {
    assert.equal(mergeUptimeSeverities(['healthy', 'warning']), 'warning')
    assert.equal(mergeUptimeSeverities(['healthy', 'critical', 'warning']), 'critical')
    assert.equal(mergeUptimeSeverities(['unknown', 'unknown']), 'unknown')
  })
})
