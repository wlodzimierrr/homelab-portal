import { cn } from '@/lib/utils'
import { isMetricStale } from '@/lib/uptime-status'

export type MetricSeverity = 'healthy' | 'warning' | 'critical' | 'unknown'

interface ServiceMetricCardProps {
  label: string
  value?: number
  formatValue: (value: number) => string
  lastRefreshedAt?: string
  severity: MetricSeverity
  noData?: boolean
  isLoading?: boolean
  staleAfterMinutes?: number
}

function getSeverityTone(severity: MetricSeverity) {
  if (severity === 'healthy') {
    return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  }
  if (severity === 'warning') {
    return 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
  }
  if (severity === 'critical') {
    return 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
  }
  return 'bg-muted text-muted-foreground'
}

function formatTimestamp(value?: string) {
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

export function ServiceMetricCard({
  label,
  value,
  formatValue,
  lastRefreshedAt,
  severity,
  noData = false,
  isLoading = false,
  staleAfterMinutes = 20,
}: ServiceMetricCardProps) {
  const isMissing = noData || typeof value !== 'number'
  const stale = isMetricStale(lastRefreshedAt, { healthyMin: 99.9, warningMin: 99.0, staleAfterMinutes })

  if (isLoading) {
    return (
      <article className="rounded-md border border-border bg-background p-4">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
        <div className="mt-2 h-8 w-28 animate-pulse rounded bg-muted" />
        <div className="mt-3 h-5 w-full animate-pulse rounded bg-muted" />
      </article>
    )
  }

  return (
    <article className="rounded-md border border-border bg-background p-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn('mt-2 text-2xl font-semibold', isMissing ? 'text-muted-foreground' : undefined)}>
        {isMissing ? 'No data' : formatValue(value)}
      </p>
      <div className="mt-3 flex items-center justify-between gap-2">
        <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium capitalize', getSeverityTone(severity))}>
          {isMissing ? 'No data' : severity}
        </span>
        <span className="text-xs text-muted-foreground">
          Last refresh: {formatTimestamp(lastRefreshedAt)}
          {stale ? ' (stale)' : ''}
        </span>
      </div>
    </article>
  )
}
