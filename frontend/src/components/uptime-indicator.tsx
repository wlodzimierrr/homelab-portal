import { cn } from '@/lib/utils'
import {
  classifyUptime,
  isMetricStale,
  mergeUptimeSeverities,
  type UptimeSeverity,
} from '@/lib/uptime-status'

interface UptimeIndicatorProps {
  uptime24h?: number
  uptime7d?: number
  lastRefreshedAt?: string
  isLoading?: boolean
  className?: string
}

function toneForSeverity(severity: UptimeSeverity) {
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

function formatPct(value?: number) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'No data'
  }
  return `${value.toFixed(2)}%`
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

export function UptimeIndicator({ uptime24h, uptime7d, lastRefreshedAt, isLoading, className }: UptimeIndicatorProps) {
  const severity24h = classifyUptime(uptime24h)
  const severity7d = classifyUptime(uptime7d)
  const combinedSeverity = mergeUptimeSeverities([severity24h, severity7d])
  const stale = isMetricStale(lastRefreshedAt)
  const hasData = typeof uptime24h === 'number' || typeof uptime7d === 'number'

  if (isLoading) {
    return (
      <article className={cn('rounded-md border border-border bg-background p-4', className)}>
        <p className="text-xs uppercase tracking-wide text-muted-foreground">Uptime</p>
        <div className="mt-3 h-4 w-32 animate-pulse rounded bg-muted" />
        <div className="mt-2 h-4 w-24 animate-pulse rounded bg-muted" />
      </article>
    )
  }

  return (
    <article className={cn('rounded-md border border-border bg-background p-4', className)}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">Uptime</p>
        <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium capitalize', toneForSeverity(combinedSeverity))}>
          {hasData ? combinedSeverity : 'No data'}
        </span>
      </div>
      <div className="grid gap-2 text-sm sm:grid-cols-2">
        <p>
          <span className="text-muted-foreground">24h:</span> {formatPct(uptime24h)}
        </p>
        <p>
          <span className="text-muted-foreground">7d:</span> {formatPct(uptime7d)}
        </p>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Last refresh: {formatTimestamp(lastRefreshedAt)}
        {stale ? ' (stale)' : ''}
      </p>
    </article>
  )
}
