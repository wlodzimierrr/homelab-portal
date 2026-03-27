import type { DeploymentObservabilityMetricSnapshot } from '@/lib/adapters/deployment-observability'
import type { DeploymentHistoryItem, DeploymentEvidenceSource } from '@/lib/adapters/deployments'
import type { LogsQuickViewPreset } from '@/lib/adapters/logs-quickview'

export type ImpactFilterMode = 'all' | 'regressions' | 'missing'
export type SortMode = 'newest' | 'worst_impact'
export type DeploymentLogsPreset = LogsQuickViewPreset

export const deploymentLogsPresetOptions: Array<{ value: DeploymentLogsPreset; label: string }> = [
  { value: 'errors', label: 'Errors' },
  { value: 'warnings', label: 'Warnings' },
  { value: 'restarts', label: 'Restarts' },
]

export function formatTimestamp(value?: string) {
  if (!value) {
    return 'N/A'
  }

  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return 'N/A'
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)
}

export function normalizeServiceId(rawServiceId: string) {
  try {
    return decodeURIComponent(rawServiceId)
  } catch {
    return rawServiceId
  }
}

export function formatAction(action: string) {
  if (action === 'config-change') {
    return 'Config change'
  }
  return action.charAt(0).toUpperCase() + action.slice(1)
}

export function formatDelta(unit: 'pct' | 'ms', value?: number) {
  if (typeof value !== 'number') {
    return 'N/A'
  }

  const signed = value >= 0 ? `+${value.toFixed(unit === 'ms' ? 0 : 2)}` : value.toFixed(unit === 'ms' ? 0 : 2)
  if (unit === 'ms') {
    return `${signed} ms`
  }

  return `${signed} pp`
}

export function formatBeforeAfter(unit: 'pct' | 'ms', before?: number, after?: number) {
  if (typeof before !== 'number' || typeof after !== 'number') {
    return 'Unavailable'
  }

  if (unit === 'ms') {
    return `${before.toFixed(0)} -> ${after.toFixed(0)} ms`
  }

  return `${before.toFixed(2)}% -> ${after.toFixed(2)}%`
}

export function formatWindowRange(start?: string, end?: string) {
  if (!start || !end) {
    return 'Window unavailable'
  }
  return `${formatTimestamp(start)} -> ${formatTimestamp(end)}`
}

export function isDeploymentRecordId(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
}

export function shiftTimestamp(value: string, minutes: number) {
  const timestamp = Date.parse(value)
  if (Number.isNaN(timestamp)) {
    return null
  }

  return new Date(timestamp + minutes * 60_000).toISOString()
}

export function buildDeploymentObservabilityRequest(item: DeploymentHistoryItem) {
  if (isDeploymentRecordId(item.id)) {
    return { deploymentId: item.id }
  }

  const windowStart = item.requestedAt ?? item.deployedAt
  const windowEnd = item.deployedAt ?? item.requestedAt

  if (windowStart && windowEnd) {
    const startMs = Date.parse(windowStart)
    const endMs = Date.parse(windowEnd)

    if (!Number.isNaN(startMs) && !Number.isNaN(endMs) && endMs > startMs) {
      return { windowStart, windowEnd }
    }

    const widenedWindowEnd = shiftTimestamp(windowStart, 5)
    if (widenedWindowEnd) {
      return { windowStart, windowEnd: widenedWindowEnd }
    }
  }

  if (windowStart) {
    const widenedWindowEnd = shiftTimestamp(windowStart, 5)
    if (widenedWindowEnd) {
      return { windowStart, windowEnd: widenedWindowEnd }
    }
  }

  if (windowEnd) {
    const widenedWindowStart = shiftTimestamp(windowEnd, -5)
    if (widenedWindowStart) {
      return { windowStart: widenedWindowStart, windowEnd }
    }
  }

  return { deploymentId: item.id }
}

export type { DeploymentObservabilityMetricSnapshot, DeploymentEvidenceSource, DeploymentHistoryItem }
