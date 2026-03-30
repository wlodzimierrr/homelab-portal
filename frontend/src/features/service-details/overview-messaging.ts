import type { ServiceMetricsSummary } from '@/lib/adapters/service-metrics'
import type { NormalizedServiceCapabilities } from './normalizers/service-detail-normalizer'

type ObservabilityMode = 'app-native' | 'ingress-derived' | 'no-http' | undefined

interface OverviewMessageOptions {
  version?: string
  observabilityMode?: ObservabilityMode
  capabilities: NormalizedServiceCapabilities
  metrics: ServiceMetricsSummary
}

function hasReleaseMetadata(version?: string) {
  return Boolean(version && version !== 'N/A')
}

function isDeployableService(version: string | undefined, capabilities: NormalizedServiceCapabilities) {
  return (
    hasReleaseMetadata(version) ||
    capabilities.canDeployToDev ||
    capabilities.canPromoteToProd ||
    capabilities.canRollback
  )
}

export function buildDeploymentMetadataMessage({
  version,
  capabilities,
}: Pick<OverviewMessageOptions, 'version' | 'capabilities'>) {
  if (hasReleaseMetadata(version)) {
    return 'Resolved from live release metadata.'
  }

  if (!isDeployableService(version, capabilities)) {
    return 'This service is tracked via live runtime and ingress health. Release history is not available for runtime-only or upstream-image services.'
  }

  return 'Deployment metadata is not available for this deployable service yet.'
}

export function buildOverviewMetricsCoverageMessage({
  version,
  observabilityMode,
  capabilities,
  metrics,
}: OverviewMessageOptions) {
  const mode = metrics.observabilityDiagnostics?.mode ?? observabilityMode
  const status = metrics.observabilityDiagnostics?.status
  const missingRequestMetrics = metrics.noData.p95LatencyMs || metrics.noData.errorRatePct

  if (mode === 'no-http') {
    return 'This service does not expose HTTP request metrics. Runtime and infrastructure health are shown where available.'
  }

  if (!missingRequestMetrics && status !== 'misconfigured' && status !== 'no_retained_data') {
    return ''
  }

  if (mode === 'ingress-derived') {
    if (status === 'misconfigured') {
      return 'Runtime status is available. Ingress-derived metrics depend on routed traffic and matching ingress labels.'
    }

    return 'Runtime status is available. Latency and error metrics for ingress-derived services require recent ingress traffic.'
  }

  if (mode === 'app-native') {
    if (isDeployableService(version, capabilities)) {
      return 'Deployment metadata is available, but app-native metrics are not configured or not currently producing matching data.'
    }

    return 'This service is expected to expose app-native metrics, but the configured source is not currently producing matching data.'
  }

  return metrics.observabilityDiagnostics?.message ?? ''
}
