import type { DeploymentObservabilityMetricSnapshot } from '@/lib/adapters/deployment-observability'
import type { DeploymentHistoryItem, DeploymentEvidenceSource } from '@/lib/adapters/deployments'
import type { LogsQuickViewPreset } from '@/lib/adapters/logs-quickview'
import { evaluateDeploymentHistoryItem } from '@/lib/deployment-alerts'
import { cn } from '@/lib/utils'

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

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const normalized = outcome.toLowerCase()
  const tone =
    normalized === 'live' || normalized === 'succeeded' || normalized === 'healthy'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : normalized === 'deploying' || normalized === 'pending'
        ? 'bg-sky-500/10 text-sky-700 dark:text-sky-300'
        : normalized === 'failed' || normalized === 'degraded' || normalized === 'error'
          ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
          : 'bg-muted text-muted-foreground'

  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>
      {outcome}
    </span>
  )
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

export function ObservabilityStatusBadge({
  status,
}: {
  status: 'ok' | 'no_data' | 'no_deployment_window'
}) {
  const tone =
    status === 'ok'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : status === 'no_data'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-slate-500/10 text-slate-700 dark:text-slate-300'
  const label =
    status === 'ok' ? 'Window scoped' : status === 'no_data' ? 'No telemetry retained' : 'No deploy window'
  return <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>{label}</span>
}

export function DeploymentMetricWindowCard({
  label,
  snapshot,
}: {
  label: string
  snapshot?: DeploymentObservabilityMetricSnapshot
}) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-2 text-sm">{formatBeforeAfter(label.includes('Latency') ? 'ms' : 'pct', snapshot?.before, snapshot?.after)}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        delta: {formatDelta(label.includes('Latency') ? 'ms' : 'pct', snapshot?.delta)}
      </p>
    </div>
  )
}

export function MetricsSourceBadge({ source }: { source: DeploymentHistoryItem['metricsSource'] }) {
  if (source === 'live_query') {
    return null
  }
  if (source === 'stored_snapshot') {
    return (
      <span className="inline-flex items-center rounded-full bg-indigo-500/10 px-2 py-1 text-xs font-medium text-indigo-700 dark:text-indigo-300">
        Stored snapshot
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded-full bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
      No retained snapshot
    </span>
  )
}

export function EvidenceBadge({
  source,
  hasData,
  backfilled,
}: {
  source: DeploymentEvidenceSource
  hasData: boolean
  backfilled?: boolean
}) {
  if (source === 'deployment_record') {
    return (
      <span className="inline-flex items-center rounded-full bg-emerald-500/10 px-2 py-1 text-xs font-medium text-emerald-700 dark:text-emerald-300">
        {backfilled ? 'Deployed · Backfilled' : 'Deployed'}
      </span>
    )
  }

  if (hasData) {
    return (
      <span className="inline-flex items-center rounded-full bg-sky-500/10 px-2 py-1 text-xs font-medium text-sky-700 dark:text-sky-300">
        Published
      </span>
    )
  }

  return (
    <span className="inline-flex items-center rounded-full bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
      Unknown deployment evidence
    </span>
  )
}

export function ImpactBadge({ item }: { item: DeploymentHistoryItem }) {
  const alert = evaluateDeploymentHistoryItem(item)

  if (!item.hasComparisonWindow && !alert.suspicious) {
    return (
      <span className="inline-flex items-center rounded-full bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
        No comparison samples
      </span>
    )
  }

  const tone =
    alert.level === 'critical'
      ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
      : alert.level === 'warning'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'

  const label =
    alert.level === 'critical'
      ? 'High regression'
      : alert.level === 'warning'
        ? 'Regression'
        : 'Stable/Improved'

  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>
      {label}
    </span>
  )
}
