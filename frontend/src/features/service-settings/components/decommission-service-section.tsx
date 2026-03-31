import { Button } from '@/components/ui/button'
import type { ServiceDecommissionResponse } from '@/lib/api'

interface DecommissionServiceSectionProps {
  serviceId: string
  mode: 'standalone' | 'project-component' | 'unsupported'
  reason: string | null
  confirmationValue: string
  setConfirmationValue: (value: string) => void
  submitting: boolean
  error: string
  result: ServiceDecommissionResponse | null
  onSubmit: () => void
}

export function DecommissionServiceSection({
  serviceId,
  mode,
  reason,
  confirmationValue,
  setConfirmationValue,
  submitting,
  error,
  result,
  onSubmit,
}: DecommissionServiceSectionProps) {
  const eligible = mode !== 'unsupported'
  const confirmationMatches = confirmationValue.trim() === serviceId
  const actionDisabled = !eligible || !confirmationMatches || submitting || result !== null
  const isProjectComponent = mode === 'project-component'
  const actionLabel = isProjectComponent ? 'Remove service from project' : 'Decommission service'

  return (
    <section className="space-y-3 rounded-md border border-rose-500/40 bg-rose-500/5 p-4">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold text-rose-700 dark:text-rose-300">Danger Zone</h2>
        {isProjectComponent ? (
          <>
            <p className="text-xs text-muted-foreground">
              Removing this service from the project opens a GitOps PR that deletes only this service&apos;s
              owned manifests and catalog entry.
            </p>
            <p className="text-xs text-muted-foreground">
              The shared project, namespace, sibling services, source repo, and GHCR artifacts stay in place.
            </p>
          </>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              Decommission removes this service from platform-managed workloads and catalog state by opening a
              GitOps PR. After that PR is merged, Argo can prune the service resources from the cluster.
            </p>
            <p className="text-xs text-muted-foreground">
              This v1 flow does not delete the source repository or GHCR package/images.
            </p>
          </>
        )}
      </div>

      {eligible ? (
        <>
          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted-foreground">
              Type <code>{serviceId}</code>
              {isProjectComponent
                ? ' to confirm removing this service from the project'
                : ' to confirm decommissioning'}
            </span>
            <input
              value={confirmationValue}
              onChange={(e) => setConfirmationValue(e.target.value)}
              placeholder={serviceId}
              className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              disabled={submitting || result !== null}
            />
          </label>

          <Button type="button" variant="outline" onClick={onSubmit} disabled={actionDisabled}>
            {submitting
              ? isProjectComponent
                ? 'Creating removal PR...'
                : 'Creating decommission PR...'
              : actionLabel}
          </Button>
        </>
      ) : (
        <div className="space-y-2 rounded-md border border-border bg-background/60 p-3">
          <p className="text-sm text-muted-foreground">{reason || 'Decommissioning is not available for this service yet.'}</p>
          <Button type="button" variant="outline" disabled>
            Decommission not available
          </Button>
        </div>
      )}

      {error ? <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p> : null}

      {result ? (
        <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
          <p className="font-medium text-emerald-900 dark:text-emerald-200">
            {isProjectComponent ? 'Project removal PR created' : 'Decommission PR created'}
          </p>
          <p className="mt-1 text-emerald-900 dark:text-emerald-200">{result.message}</p>
          <a
            href={result.prUrl}
            target="_blank"
            rel="noreferrer"
            className="mt-2 block text-xs text-primary hover:underline"
          >
            View decommission PR
          </a>
          {result.preservedArtifacts.length > 0 ? (
            <p className="mt-2 text-xs text-muted-foreground">
              Preserved in v1: {result.preservedArtifacts.join(', ')}.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
