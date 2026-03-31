import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildDeploymentMetadataMessage,
  buildOverviewMetricsCoverageMessage,
} from '../src/features/service-details/overview-messaging.js'
import type { NormalizedServiceCapabilities } from '../src/features/service-details/normalizers/service-detail-normalizer.js'

const emptyCapabilities: NormalizedServiceCapabilities = {
  canDeployToDev: false,
  canPromoteToProd: false,
  canRollback: false,
  rollbackEnvs: [],
  canEditConfig: false,
  configEnvs: [],
  canEditPublicHostname: false,
  canAdopt: true,
  canDelete: false,
  decommissionMode: 'unsupported',
  decommissionReason: null,
}

function createMetrics(overrides: Partial<Parameters<typeof buildOverviewMetricsCoverageMessage>[0]['metrics']> = {}) {
  return {
    serviceId: 'service-a',
    range: '24h' as const,
    generatedAt: '2026-03-30T12:00:00Z',
    noData: {
      uptimePct: false,
      p95LatencyMs: true,
      errorRatePct: true,
      restartCount: false,
    },
    observabilityDiagnostics: {
      missingMetrics: [],
    },
    ...overrides,
  }
}

test('buildDeploymentMetadataMessage explains runtime-only services without release history', () => {
  assert.equal(
    buildDeploymentMetadataMessage({
      version: 'N/A',
      capabilities: emptyCapabilities,
    }),
    'This service is tracked via live runtime and ingress health. Release history is not available for runtime-only or upstream-image services.',
  )
})

test('buildDeploymentMetadataMessage keeps deployable services honest when metadata is missing', () => {
  assert.equal(
    buildDeploymentMetadataMessage({
      version: 'N/A',
      capabilities: {
        ...emptyCapabilities,
        canDeployToDev: true,
      },
    }),
    'Deployment metadata is not available for this deployable service yet.',
  )
})

test('buildOverviewMetricsCoverageMessage explains ingress-derived services without recent traffic', () => {
  assert.equal(
    buildOverviewMetricsCoverageMessage({
      version: 'N/A',
      observabilityMode: 'ingress-derived',
      capabilities: emptyCapabilities,
      metrics: createMetrics({
        observabilityDiagnostics: {
          mode: 'ingress-derived',
          status: 'no_retained_data',
          missingMetrics: [],
        },
      }),
    }),
    'Runtime status is available. Latency and error metrics for ingress-derived services require recent ingress traffic.',
  )
})

test('buildOverviewMetricsCoverageMessage explains deployable services with missing app-native metrics', () => {
  assert.equal(
    buildOverviewMetricsCoverageMessage({
      version: 'sha-1234567',
      observabilityMode: 'app-native',
      capabilities: {
        ...emptyCapabilities,
        canDeployToDev: true,
      },
      metrics: createMetrics({
        observabilityDiagnostics: {
          mode: 'app-native',
          status: 'misconfigured',
          missingMetrics: ['http_requests_total'],
        },
      }),
    }),
    'Deployment metadata is available, but app-native metrics are not configured or not currently producing matching data.',
  )
})

test('buildOverviewMetricsCoverageMessage stays empty when summary metrics are present', () => {
  assert.equal(
    buildOverviewMetricsCoverageMessage({
      version: 'sha-1234567',
      observabilityMode: 'ingress-derived',
      capabilities: {
        ...emptyCapabilities,
        canDeployToDev: true,
      },
      metrics: createMetrics({
        p95LatencyMs: 120,
        errorRatePct: 0.2,
        noData: {
          uptimePct: false,
          p95LatencyMs: false,
          errorRatePct: false,
          restartCount: false,
        },
        observabilityDiagnostics: {
          mode: 'ingress-derived',
          status: 'ok',
          missingMetrics: [],
        },
      }),
    }),
    '',
  )
})
