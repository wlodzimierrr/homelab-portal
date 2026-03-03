import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { getDeploymentHistory, type DeploymentHistoryItem } from '@/lib/adapters/deployments'
import { cn } from '@/lib/utils'

interface ServiceDeploymentsPageProps {
  serviceId: string
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

function normalizeServiceId(rawServiceId: string) {
  try {
    return decodeURIComponent(rawServiceId)
  } catch {
    return rawServiceId
  }
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const normalized = outcome.toLowerCase()
  const tone =
    normalized === 'succeeded' || normalized === 'healthy'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : normalized === 'failed' || normalized === 'degraded' || normalized === 'error'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-muted text-muted-foreground'

  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>
      {outcome}
    </span>
  )
}

export function ServiceDeploymentsPage({ serviceId }: ServiceDeploymentsPageProps) {
  const normalizedServiceId = useMemo(() => normalizeServiceId(serviceId), [serviceId])
  const [deployments, setDeployments] = useState<DeploymentHistoryItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  const loadDeployments = useCallback(async () => {
    setIsLoading(true)
    setError('')
    try {
      const history = await getDeploymentHistory(normalizedServiceId, { limit: 10 })
      setDeployments(history)
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load deployment history'
      setError(message)
      setDeployments([])
    } finally {
      setIsLoading(false)
    }
  }, [normalizedServiceId])

  useEffect(() => {
    void loadDeployments()
  }, [loadDeployments])

  return (
    <PageShell
      title={`Deployments: ${normalizedServiceId || 'unknown'}`}
      description="Read-only deployment timeline sourced from adapter-backed data."
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">Showing the 10 most recent deployments.</p>
          <AppLink
            to={`/services/${encodeURIComponent(normalizedServiceId)}`}
            className="text-sm font-medium text-primary hover:underline"
          >
            Back to overview
          </AppLink>
        </div>

        {isLoading ? <LoadingState label="Loading deployments..." rows={5} /> : null}
        {!isLoading && error ? <ErrorState message={error} onRetry={() => void loadDeployments()} /> : null}
        {!isLoading && !error && deployments.length === 0 ? (
          <EmptyState title="No deployments found for this service yet." />
        ) : null}
        {!isLoading && !error && deployments.length > 0 ? (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="min-w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="px-3 py-2 font-medium text-muted-foreground">Deployed At</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Version / Tag</th>
                  <th className="px-3 py-2 font-medium text-muted-foreground">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {deployments.map((item) => (
                  <tr key={item.id} className="border-b border-border/70">
                    <td className="px-3 py-2 text-muted-foreground">{formatTimestamp(item.deployedAt)}</td>
                    <td className="px-3 py-2">{item.version}</td>
                    <td className="px-3 py-2">
                      <OutcomeBadge outcome={item.outcome} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </PageShell>
  )
}
