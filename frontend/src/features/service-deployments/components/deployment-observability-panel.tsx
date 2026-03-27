import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { ServiceHealthTimeline } from '@/components/service-health-timeline'
import type { DeploymentObservability } from '@/lib/adapters/deployment-observability'
import type { DeploymentHistoryItem } from '@/lib/adapters/deployments'
import {
  deploymentLogsPresetOptions,
  formatAction,
  formatTimestamp,
  formatWindowRange,
  type DeploymentLogsPreset,
} from '../shared'
import {
  DeploymentMetricWindowCard,
  ObservabilityStatusBadge,
  OutcomeBadge,
} from '../shared-components'

interface DeploymentObservabilityPanelProps {
  deployments: DeploymentHistoryItem[]
  selectedDeployment: DeploymentHistoryItem | null
  observability: DeploymentObservability | null
  observabilityLoading: boolean
  observabilityError: string
  logsPreset: DeploymentLogsPreset
  setLogsPreset: (value: DeploymentLogsPreset) => void
  loadDeploymentObservability: () => Promise<void>
}

export function DeploymentObservabilityPanel({
  deployments,
  selectedDeployment,
  observability,
  observabilityLoading,
  observabilityError,
  logsPreset,
  setLogsPreset,
  loadDeploymentObservability,
}: DeploymentObservabilityPanelProps) {
  if (deployments.length === 0) {
    return null
  }

  return (
    <section className="space-y-3 rounded-md border border-border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Deploy Window Observability</h2>
          <p className="text-xs text-muted-foreground">
            Logs, metrics, and health timeline anchored to the selected deployment record.
          </p>
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <p>
            {selectedDeployment
              ? `${formatAction(selectedDeployment.action)} ${selectedDeployment.version}`
              : 'No deployment selected'}
          </p>
          <p>
            {observability?.context
              ? formatWindowRange(
                  observability.context.windowStart,
                  observability.context.windowEnd,
                )
              : 'Select a deployment row to inspect.'}
          </p>
        </div>
      </div>

      {selectedDeployment ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <OutcomeBadge outcome={selectedDeployment.outcome} />
          {observability ? (
            <ObservabilityStatusBadge status={observability.metrics.queryStatus} />
          ) : null}
          {observability?.context.deployReason ? (
            <span className="text-muted-foreground">{observability.context.deployReason}</span>
          ) : null}
        </div>
      ) : null}

      {observabilityLoading ? (
        <LoadingState label="Loading deploy-window observability..." rows={4} />
      ) : null}
      {!observabilityLoading && observabilityError ? (
        <ErrorState
          message={observabilityError}
          onRetry={() => void loadDeploymentObservability()}
        />
      ) : null}

      {!observabilityLoading && !observabilityError && observability ? (
        <div className="space-y-4">
          {observability.context.evidenceStatus === 'missing' ? (
            <div className="rounded-md border border-slate-500/40 bg-slate-500/10 p-3">
              <p className="text-sm font-medium text-slate-900 dark:text-slate-200">
                Deployment window evidence is missing for this record.
              </p>
              <p className="mt-1 text-xs text-slate-900 dark:text-slate-200">
                {observability.context.evidenceMessage ??
                  'This deployment record does not expose a usable deploy window yet.'}
              </p>
            </div>
          ) : null}

          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold">Anchored Metrics</h3>
              <ObservabilityStatusBadge status={observability.metrics.queryStatus} />
            </div>
            {observability.metrics.queryMessage ? (
              <p className="text-xs text-muted-foreground">
                {observability.metrics.queryMessage}
              </p>
            ) : null}
            <div className="grid gap-3 md:grid-cols-3">
              <DeploymentMetricWindowCard
                label="Error Rate"
                snapshot={observability.metrics.errorRatePct}
              />
              <DeploymentMetricWindowCard
                label="P95 Latency"
                snapshot={observability.metrics.p95LatencyMs}
              />
              <DeploymentMetricWindowCard
                label="Availability"
                snapshot={observability.metrics.availabilityPct}
              />
            </div>
          </section>

          <section className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold">Deploy Health Timeline</h3>
              <ObservabilityStatusBadge status={observability.healthTimeline.queryStatus} />
            </div>
            {observability.healthTimeline.queryMessage ? (
              <p className="text-xs text-muted-foreground">
                {observability.healthTimeline.queryMessage}
              </p>
            ) : null}
            <ServiceHealthTimeline
              segments={observability.healthTimeline.segments}
              lastRefreshedAt={observability.healthTimeline.generatedAt}
              isLoading={false}
            />
          </section>

          <section className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-sm font-semibold">Deploy Logs Quick View</h3>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                Preset
                <select
                  value={logsPreset}
                  onChange={(event) =>
                    setLogsPreset(event.target.value as DeploymentLogsPreset)
                  }
                  className="rounded-md border border-border bg-background px-2 py-1 text-xs"
                >
                  {deploymentLogsPresetOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <ObservabilityStatusBadge status={observability.logsQuickView.queryStatus} />
              {observability.logsQuickView.queryMessage ? (
                <p className="text-xs text-muted-foreground">
                  {observability.logsQuickView.queryMessage}
                </p>
              ) : null}
            </div>
            {observability.logsQuickView.lines.length > 0 ? (
              <div className="overflow-hidden rounded-md border border-border">
                <div className="max-h-80 overflow-auto">
                  <table className="min-w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-border bg-muted/30">
                        <th className="px-3 py-2 font-medium text-muted-foreground">Timestamp</th>
                        <th className="px-3 py-2 font-medium text-muted-foreground">Message</th>
                      </tr>
                    </thead>
                    <tbody>
                      {observability.logsQuickView.lines.map((line) => (
                        <tr
                          key={`${line.timestamp}-${line.message}`}
                          className="border-b border-border/50 align-top"
                        >
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            {formatTimestamp(line.timestamp)}
                          </td>
                          <td className="px-3 py-2">
                            <pre className="whitespace-pre-wrap break-words font-mono text-xs">
                              {line.message}
                            </pre>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-border bg-muted/10 p-3 text-sm text-muted-foreground">
                No log lines were retained for this deployment window and preset.
              </div>
            )}
          </section>
        </div>
      ) : null}
    </section>
  )
}
