import { Button } from '@/components/ui/button'
import type { DeploymentHistoryItem } from '@/lib/adapters/deployments'
import type {
  ServiceDeployToDevResponse,
  ServicePromoteToProdResponse,
  ServiceRollbackCandidatesResponse,
  ServiceRollbackResponse,
} from '@/lib/api/deployments'
import { getRecentDeploymentTags } from '../shared'

interface DeploymentActionsPanelProps {
  serviceId: string
  deploymentHistory: DeploymentHistoryItem[]
  rollbackSupported: boolean
  latestDevDeployment?: DeploymentHistoryItem
  latestProdDeployment?: DeploymentHistoryItem
  recentDevTags: string[]
  recentProdTags: string[]
  devLockActive: boolean
  prodLockActive: boolean
  devInFlight: boolean
  prodInFlight: boolean
  deployReason: string
  setDeployReason: (value: string) => void
  deploySubmitting: boolean
  deployError: string
  deployResult: ServiceDeployToDevResponse | null
  onSubmitDeploy: () => void
  promoteReason: string
  setPromoteReason: (value: string) => void
  promoteSubmitting: boolean
  promoteError: string
  promoteResult: ServicePromoteToProdResponse | null
  onSubmitPromote: () => void
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

export function DeploymentActionsPanel({
  serviceId,
  deploymentHistory,
  rollbackSupported,
  latestDevDeployment,
  latestProdDeployment,
  recentDevTags,
  recentProdTags,
  devLockActive,
  prodLockActive,
  devInFlight,
  prodInFlight,
  deployReason,
  setDeployReason,
  deploySubmitting,
  deployError,
  deployResult,
  onSubmitDeploy,
  promoteReason,
  setPromoteReason,
  promoteSubmitting,
  promoteError,
  promoteResult,
  onSubmitPromote,
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
}: DeploymentActionsPanelProps) {
  if (!rollbackSupported) {
    return null
  }

  return (
    <>
      <section className="space-y-3 rounded-md border border-border bg-card p-4">
        <div className="space-y-1">
          <h2 className="text-sm font-semibold">Portal Actions</h2>
          <p className="text-xs text-muted-foreground">
            Request deploy and promote actions directly from the service page. Buttons are blocked while a
            deployment lock or in-flight deployment exists for the target environment.
          </p>
        </div>
        <div className="grid gap-4 xl:grid-cols-2">
          <article className="space-y-3 rounded-md border border-border/70 bg-background/50 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold">Deploy latest to dev</h3>
                <p className="text-xs text-muted-foreground">
                  Opens a GitOps PR that updates the dev overlay to the newest deployable image.
                </p>
              </div>
              {devLockActive || devInFlight ? (
                <span className="rounded-full bg-sky-500/10 px-2 py-1 text-xs font-medium text-sky-700 dark:text-sky-300">
                  Dev locked
                </span>
              ) : null}
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-md border border-border/70 bg-background p-3 text-xs text-muted-foreground">
                <p className="font-medium text-foreground">Current dev tag</p>
                <p className="mt-1 break-all">{latestDevDeployment?.version ?? 'Unavailable'}</p>
              </div>
              <div className="rounded-md border border-border/70 bg-background p-3 text-xs text-muted-foreground">
                <p className="font-medium text-foreground">Recent dev tags</p>
                <div className="mt-1 flex flex-wrap gap-2">
                  {recentDevTags.map((tag) => (
                    <code key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
                      {tag}
                    </code>
                  ))}
                  {recentDevTags.length === 0 ? <span>Unavailable</span> : null}
                </div>
              </div>
            </div>
            <label className="space-y-1">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Deploy reason</span>
              <textarea
                value={deployReason}
                onChange={(event) => setDeployReason(event.target.value)}
                rows={3}
                placeholder="Why the latest build should be deployed to dev."
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            {deployError ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3">
                <p className="text-xs text-destructive">{deployError}</p>
              </div>
            ) : null}
            {deployResult ? (
              <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-950 dark:text-emerald-200">
                <p className="font-medium">
                  {deployResult.status === 'noop'
                    ? deployResult.message ?? 'Dev already points at the latest deployable image.'
                    : `Deploy request accepted for ${serviceId}.`}
                </p>
                {deployResult.gitPrUrl ? (
                  <a href={deployResult.gitPrUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex font-medium underline underline-offset-2">
                    Open GitOps PR #{deployResult.gitPrNumber ?? 'link'}
                  </a>
                ) : null}
              </div>
            ) : null}
            <Button
              type="button"
              onClick={onSubmitDeploy}
              disabled={deploySubmitting || devLockActive || devInFlight || deployReason.trim().length < 5}
            >
              {deploySubmitting ? 'Requesting deploy...' : 'Deploy latest to dev'}
            </Button>
          </article>

          <article className="space-y-3 rounded-md border border-border/70 bg-background/50 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold">Promote dev to prod</h3>
                <p className="text-xs text-muted-foreground">
                  Opens a GitOps PR that copies the current dev tag into the prod overlay for this service.
                </p>
              </div>
              {prodLockActive || prodInFlight ? (
                <span className="rounded-full bg-sky-500/10 px-2 py-1 text-xs font-medium text-sky-700 dark:text-sky-300">
                  Prod locked
                </span>
              ) : null}
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-md border border-border/70 bg-background p-3 text-xs text-muted-foreground">
                <p className="font-medium text-foreground">Current dev tag</p>
                <p className="mt-1 break-all">{latestDevDeployment?.version ?? 'Unavailable'}</p>
              </div>
              <div className="rounded-md border border-border/70 bg-background p-3 text-xs text-muted-foreground">
                <p className="font-medium text-foreground">Current prod tag</p>
                <p className="mt-1 break-all">{latestProdDeployment?.version ?? 'Unavailable'}</p>
              </div>
            </div>
            <div className="rounded-md border border-border/70 bg-background p-3 text-xs text-muted-foreground">
              <p className="font-medium text-foreground">Recent prod tags</p>
              <div className="mt-1 flex flex-wrap gap-2">
                {recentProdTags.map((tag) => (
                  <code key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
                    {tag}
                  </code>
                ))}
                {recentProdTags.length === 0 ? <span>Unavailable</span> : null}
              </div>
            </div>
            <label className="space-y-1">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Promote reason</span>
              <textarea
                value={promoteReason}
                onChange={(event) => setPromoteReason(event.target.value)}
                rows={3}
                placeholder="Why the current dev tag should be promoted to prod."
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
            </label>
            {promoteError ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3">
                <p className="text-xs text-destructive">{promoteError}</p>
              </div>
            ) : null}
            {promoteResult ? (
              <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-950 dark:text-emerald-200">
                <p className="font-medium">
                  {promoteResult.status === 'noop'
                    ? promoteResult.message ?? 'Prod already matches dev.'
                    : `Promote request accepted for ${serviceId}.`}
                </p>
                {promoteResult.gitPrUrl ? (
                  <a href={promoteResult.gitPrUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex font-medium underline underline-offset-2">
                    Open GitOps PR #{promoteResult.gitPrNumber ?? 'link'}
                  </a>
                ) : null}
              </div>
            ) : null}
            <Button
              type="button"
              onClick={onSubmitPromote}
              disabled={promoteSubmitting || prodLockActive || prodInFlight || promoteReason.trim().length < 5}
            >
              {promoteSubmitting ? 'Requesting promote...' : 'Promote to prod'}
            </Button>
          </article>
        </div>
      </section>

      <section className="space-y-3 rounded-md border border-border bg-card p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-sm font-semibold">Portal Rollback</h2>
            <p className="text-xs text-muted-foreground">
              Request a Git-backed rollback for this service. The portal lists previous deployable tags from
              GitHub Packages, opens a rollback PR, and records the request as a first-class deployment event.
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
              <option value="dev">dev</option>
              <option value="prod">prod</option>
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
              {getRecentDeploymentTags(deploymentHistory, rollbackTargetEnvironment).map((tag) => (
                <code key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
                  {tag}
                </code>
              ))}
              {getRecentDeploymentTags(deploymentHistory, rollbackTargetEnvironment).length === 0 ? (
                <span>Unavailable</span>
              ) : null}
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
            Existing rollback records remain visible in the deployment history view for operator audit.
          </p>
        </div>
      </section>
    </>
  )
}
