import { Button } from '@/components/ui/button'
import type { DeploymentHistoryItem } from '@/lib/adapters/deployments'
import type {
  ServiceRollbackCandidatesResponse,
  ServiceRollbackResponse,
} from '@/lib/api/deployments'
import { getRecentDeploymentTags } from '@/features/service-details/shared'

interface RollbackPanelProps {
  rollbackSupported: boolean
  rollbackEnvs: Array<'dev' | 'prod'>
  deploymentHistory: DeploymentHistoryItem[]
  rollbackTargetEnvironment: 'dev' | 'prod'
  setRollbackTargetEnvironment: (value: 'dev' | 'prod') => void
  rollbackCandidates: ServiceRollbackCandidatesResponse | null
  rollbackCandidatesLoading: boolean
  rollbackCandidatesError: string
  selectedRollbackTag: string
  setSelectedRollbackTag: (value: string) => void
  rollbackReason: string
  setRollbackReason: (value: string) => void
  rollbackSubmitting: boolean
  rollbackError: string
  rollbackResult: ServiceRollbackResponse | null
  rollbackLockActive: boolean
  rollbackInFlight: boolean
  onSubmitRollback: () => void
}

export function RollbackPanel({
  rollbackSupported,
  rollbackEnvs,
  deploymentHistory,
  rollbackTargetEnvironment,
  setRollbackTargetEnvironment,
  rollbackCandidates,
  rollbackCandidatesLoading,
  rollbackCandidatesError,
  selectedRollbackTag,
  setSelectedRollbackTag,
  rollbackReason,
  setRollbackReason,
  rollbackSubmitting,
  rollbackError,
  rollbackResult,
  rollbackLockActive,
  rollbackInFlight,
  onSubmitRollback,
}: RollbackPanelProps) {
  if (!rollbackSupported) {
    return null
  }

  const recentTags = getRecentDeploymentTags(deploymentHistory, rollbackTargetEnvironment)

  return (
    <section className="space-y-3 rounded-md border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-sm font-semibold">Portal Rollback</h2>
          <p className="text-xs text-muted-foreground">
            Request a Git-backed rollback after reviewing deployment history and compare context for this
            service.
          </p>
        </div>
        {rollbackLockActive || rollbackInFlight ? (
          <span className="rounded-full bg-sky-500/10 px-2 py-1 text-xs font-medium text-sky-700 dark:text-sky-300">
            {rollbackTargetEnvironment === 'dev' ? 'Dev' : 'Prod'} locked
          </span>
        ) : null}
      </div>
      <div className="grid gap-3 md:grid-cols-[180px_minmax(0,1fr)]">
        <label className="space-y-1">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Target environment
          </span>
          <select
            value={rollbackTargetEnvironment}
            onChange={(event) => setRollbackTargetEnvironment(event.target.value as 'dev' | 'prod')}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            {rollbackEnvs.map((env) => (
              <option key={env} value={env}>
                {env}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Rollback target tag
          </span>
          <select
            value={selectedRollbackTag}
            onChange={(event) => setSelectedRollbackTag(event.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            disabled={rollbackCandidatesLoading || (rollbackCandidates?.candidates.length ?? 0) === 0}
          >
            {(rollbackCandidates?.candidates ?? []).map((candidate) => (
              <option key={candidate.tag} value={candidate.tag}>
                {candidate.tag}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-md border border-border/70 bg-background/50 p-3 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">Current tag</p>
          <p className="mt-1 break-all">{rollbackCandidates?.currentTag ?? 'Unavailable'}</p>
        </div>
        <div className="rounded-md border border-border/70 bg-background/50 p-3 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">Recent deployed tags</p>
          <div className="mt-1 flex flex-wrap gap-2">
            {recentTags.map((tag) => (
              <code key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
                {tag}
              </code>
            ))}
            {recentTags.length === 0 ? <span>Unavailable</span> : null}
          </div>
        </div>
      </div>
      {rollbackCandidatesError ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3">
          <p className="text-xs text-destructive">{rollbackCandidatesError}</p>
        </div>
      ) : null}
      {!rollbackCandidatesError && !rollbackCandidatesLoading && rollbackCandidates ? (
        <div className="rounded-md border border-border/70 bg-background/50 p-3 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">Available rollback candidates</p>
          {rollbackCandidates.candidates.length > 0 ? (
            <div className="mt-2 space-y-2">
              {rollbackCandidates.candidates.slice(0, 5).map((candidate) => (
                <div key={candidate.tag} className="flex flex-wrap items-center gap-2">
                  <code className="rounded bg-muted px-1.5 py-0.5 text-[11px]">{candidate.tag}</code>
                  {candidate.publishedAt ? <span>published {candidate.publishedAt}</span> : null}
                  {candidate.compareUrl ? (
                    <a
                      href={candidate.compareUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-primary hover:underline"
                    >
                      Compare
                    </a>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-2">No previous deployable tags are available right now.</p>
          )}
        </div>
      ) : null}
      <label className="space-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Reason</span>
        <textarea
          value={rollbackReason}
          onChange={(event) => setRollbackReason(event.target.value)}
          rows={3}
          placeholder="Why this rollback is needed and what known-good version you are restoring."
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
        />
      </label>
      {rollbackError ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3">
          <p className="text-xs text-destructive">{rollbackError}</p>
        </div>
      ) : null}
      {rollbackResult ? (
        <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-950 dark:text-emerald-200">
          <p className="font-medium">
            {rollbackResult.status === 'noop'
              ? `Rollback not needed for ${rollbackResult.targetEnvironment}.`
              : `Rollback request accepted for ${rollbackResult.targetEnvironment}.`}
          </p>
          {rollbackResult.gitPrUrl ? (
            <a
              href={rollbackResult.gitPrUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-flex font-medium underline underline-offset-2"
            >
              Open GitOps PR #{rollbackResult.gitPrNumber ?? 'link'}
            </a>
          ) : null}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={onSubmitRollback}
          disabled={
            rollbackSubmitting ||
            rollbackLockActive ||
            rollbackInFlight ||
            rollbackCandidatesLoading ||
            selectedRollbackTag.trim().length === 0 ||
            rollbackReason.trim().length < 5
          }
        >
          {rollbackSubmitting ? 'Requesting rollback...' : `Request ${rollbackTargetEnvironment} rollback`}
        </Button>
        <p className="text-xs text-muted-foreground">
          Existing rollback records remain visible in the deployment history table for audit and follow-up.
        </p>
      </div>
    </section>
  )
}
