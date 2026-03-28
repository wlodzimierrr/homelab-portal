import { Button } from '@/components/ui/button'
import type { DeploymentHistoryItem } from '@/lib/adapters/deployments'
import type {
  ServiceDeployToDevResponse,
  ServicePromoteToProdResponse,
} from '@/lib/api/deployments'

interface ForwardActionsPanelProps {
  serviceId: string
  deploySupported: boolean
  promoteSupported: boolean
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
}

export function ForwardActionsPanel({
  serviceId,
  deploySupported,
  promoteSupported,
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
}: ForwardActionsPanelProps) {
  if (!deploySupported && !promoteSupported) {
    return null
  }

  return (
    <section className="space-y-3 rounded-md border border-border bg-card p-4">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold">Portal Actions</h2>
        <p className="text-xs text-muted-foreground">
          Request common forward deployment actions directly from the overview page. Rollback stays with
          deployment history so operators can review context before reverting.
        </p>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {deploySupported ? (
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
                  <a
                    href={deployResult.gitPrUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-2 inline-flex font-medium underline underline-offset-2"
                  >
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
        ) : null}

        {promoteSupported ? (
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
                  <a
                    href={promoteResult.gitPrUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-2 inline-flex font-medium underline underline-offset-2"
                  >
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
        ) : null}
      </div>
    </section>
  )
}
