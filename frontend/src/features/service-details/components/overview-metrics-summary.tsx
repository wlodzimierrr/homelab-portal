import { LoadingState } from '@/components/loading-state'
import { Button } from '@/components/ui/button'
import type { ServiceMetricsSummary } from '@/lib/adapters/service-metrics'

interface OverviewMetricsSummaryProps {
  health: 'healthy' | 'degraded' | 'unknown'
  metrics: ServiceMetricsSummary
  isLoading: boolean
  error: string
  coverageMessage?: string
  onRetry: () => void
}

function formatDate(value?: string) {
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

function formatPrimaryStatus(health: OverviewMetricsSummaryProps['health']) {
  if (health === 'healthy') {
    return {
      label: 'Healthy',
      tone: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
    }
  }
  if (health === 'degraded') {
    return {
      label: 'Needs attention',
      tone: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
    }
  }
  return {
    label: 'Unknown',
    tone: 'bg-slate-500/10 text-slate-700 dark:text-slate-300',
  }
}

function formatLatency(metrics: ServiceMetricsSummary) {
  if (typeof metrics.p95LatencyMs === 'number') {
    return `${Math.round(metrics.p95LatencyMs)} ms`
  }
  if (metrics.observabilityDiagnostics?.mode === 'no-http') {
    return 'Not collected'
  }
  return 'No data'
}

function formatLatencyTone(metrics: ServiceMetricsSummary) {
  if (metrics.noData.p95LatencyMs) {
    return 'text-muted-foreground'
  }
  if ((metrics.p95LatencyMs ?? 0) > 500) {
    return 'text-rose-700 dark:text-rose-300'
  }
  if ((metrics.p95LatencyMs ?? 0) > 250) {
    return 'text-amber-700 dark:text-amber-300'
  }
  return 'text-foreground'
}

function formatErrorRate(metrics: ServiceMetricsSummary) {
  if (typeof metrics.errorRatePct === 'number') {
    return `${metrics.errorRatePct.toFixed(2)}%`
  }
  if (metrics.observabilityDiagnostics?.mode === 'no-http') {
    return 'Not collected'
  }
  return 'No data'
}

function formatErrorTone(metrics: ServiceMetricsSummary) {
  if (metrics.noData.errorRatePct) {
    return 'text-muted-foreground'
  }
  if ((metrics.errorRatePct ?? 0) > 3) {
    return 'text-rose-700 dark:text-rose-300'
  }
  if ((metrics.errorRatePct ?? 0) > 1) {
    return 'text-amber-700 dark:text-amber-300'
  }
  return 'text-foreground'
}

export function OverviewMetricsSummary({
  health,
  metrics,
  isLoading,
  error,
  coverageMessage,
  onRetry,
}: OverviewMetricsSummaryProps) {
  const status = formatPrimaryStatus(health)

  return (
    <section className="rounded-md border border-border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-sm font-semibold">Overview Metrics</h2>
          <p className="text-xs text-muted-foreground">
            Quick situational summary for health, latency, and errors.
          </p>
        </div>
        <span className={`inline-flex rounded-full px-2 py-1 text-xs font-medium ${status.tone}`}>
          {status.label}
        </span>
      </div>

      {isLoading ? (
        <div className="mt-4">
          <LoadingState label="Loading overview metrics..." rows={2} />
        </div>
      ) : (
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <div className="rounded-md border border-border/70 bg-muted/20 p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Latency</p>
            <p className={`mt-2 text-lg font-semibold ${formatLatencyTone(metrics)}`}>{formatLatency(metrics)}</p>
            <p className="mt-1 text-xs text-muted-foreground">P95 over the current summary window</p>
          </div>
          <div className="rounded-md border border-border/70 bg-muted/20 p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Error Rate</p>
            <p className={`mt-2 text-lg font-semibold ${formatErrorTone(metrics)}`}>{formatErrorRate(metrics)}</p>
            <p className="mt-1 text-xs text-muted-foreground">Request failure rate for this service</p>
          </div>
          <div className="rounded-md border border-border/70 bg-muted/20 p-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Last Refresh</p>
            <p className="mt-2 text-lg font-semibold text-foreground">{formatDate(metrics.generatedAt)}</p>
            <p className="mt-1 text-xs text-muted-foreground">From the latest portal summary fetch</p>
          </div>
        </div>
      )}

      {error ? (
        <div className="mt-4 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
          <p className="text-xs text-amber-900 dark:text-amber-200">{error}</p>
          <Button type="button" size="sm" variant="outline" className="mt-2" onClick={onRetry}>
            Retry metrics
          </Button>
        </div>
      ) : null}

      {!error && coverageMessage ? (
        <p className="mt-4 text-xs text-muted-foreground">{coverageMessage}</p>
      ) : null}
    </section>
  )
}
