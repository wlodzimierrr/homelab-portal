import { evaluateDeploymentHistoryItem } from '@/lib/deployment-alerts'
import { cn } from '@/lib/utils'
import type { DeploymentObservabilityMetricSnapshot, DeploymentEvidenceSource, DeploymentHistoryItem } from './shared'
import { formatBeforeAfter, formatDelta } from './shared'

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
