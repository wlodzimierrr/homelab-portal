import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveServiceObservabilityLoadOptions } from '../src/features/service-details/observability-load-options.js'

test('resolveServiceObservabilityLoadOptions keeps full loading enabled by default', () => {
  assert.deepEqual(resolveServiceObservabilityLoadOptions(), {
    loadMetrics: true,
    loadTrends: true,
    loadTimeline: true,
    loadLogs: true,
  })
})

test('resolveServiceObservabilityLoadOptions supports overview metrics-only mode', () => {
  assert.deepEqual(
    resolveServiceObservabilityLoadOptions({
      loadTrends: false,
      loadTimeline: false,
      loadLogs: false,
    }),
    {
      loadMetrics: true,
      loadTrends: false,
      loadTimeline: false,
      loadLogs: false,
    },
  )
})
