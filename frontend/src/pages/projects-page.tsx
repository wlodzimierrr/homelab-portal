import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import {
  getCatalogReconciliation,
  getProjectCatalogDiagnostics,
  getProjects,
  type CatalogJoinRow,
  type CatalogJoinDiagnostics,
  type Project,
  type ProjectCatalogDiagnosticsResponse,
} from '@/lib/api'
import { cn } from '@/lib/utils'

function projectAnchor(projectId: string, env: string) {
  return `${projectId}-${env}`.toLowerCase()
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

function SourceStateBadge({ state }: { state?: string }) {
  const normalized = state?.toLowerCase() ?? 'unknown'
  const tone =
    normalized === 'fresh'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : normalized === 'warning'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : normalized === 'stale'
          ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
        : normalized === 'empty'
          ? 'bg-slate-500/10 text-slate-700 dark:text-slate-300'
          : 'bg-muted text-muted-foreground'

  return <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium capitalize', tone)}>{normalized}</span>
}

function ReconciliationBadge({ diagnostics }: { diagnostics?: CatalogJoinDiagnostics }) {
  const mismatchCount =
    (diagnostics?.projectOnlyCount ?? 0) +
    (diagnostics?.serviceOnlyCount ?? 0) +
    (diagnostics?.oneToManyCount ?? 0) +
    (diagnostics?.ambiguousJoinCount ?? 0)
  const tone =
    mismatchCount === 0
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
  const label = mismatchCount === 0 ? 'Aligned' : `${mismatchCount} mismatch${mismatchCount === 1 ? '' : 'es'}`

  return <span className={cn('inline-flex rounded-full px-2 py-1 text-xs font-medium', tone)}>{label}</span>
}

function SummaryCard({ label, value, meta }: { label: string; value: string; meta?: string }) {
  return (
    <article className="rounded-md border border-border bg-background p-4">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-2 text-2xl font-semibold">{value}</p>
      {meta ? <p className="mt-1 text-xs text-muted-foreground">{meta}</p> : null}
    </article>
  )
}

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [catalogRows, setCatalogRows] = useState<CatalogJoinRow[]>([])
  const [diagnostics, setDiagnostics] = useState<ProjectCatalogDiagnosticsResponse | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  const loadProjects = useCallback(async () => {
    setIsLoading(true)
    setError('')
    setWarnings([])
    try {
      const [projectsResult, catalogResult, diagnosticsResult] = await Promise.allSettled([
        getProjects(),
        getCatalogReconciliation(),
        getProjectCatalogDiagnostics(),
      ])
      if (projectsResult.status !== 'fulfilled') {
        throw projectsResult.reason
      }

      const nextWarnings: string[] = []
      setProjects(projectsResult.value.projects)
      setCatalogRows(catalogResult.status === 'fulfilled' ? catalogResult.value.rows : [])
      if (catalogResult.status !== 'fulfilled') {
        nextWarnings.push('Project-to-service reconciliation is unavailable; project links may be incomplete.')
      }
      if (diagnosticsResult.status === 'fulfilled') {
        setDiagnostics(diagnosticsResult.value)
        if (diagnosticsResult.value.freshness.state === 'warning') {
          nextWarnings.push('Project catalog data is aging and will become stale soon if sync does not run.')
        }
        if (diagnosticsResult.value.freshness.state === 'stale') {
          nextWarnings.push('Project catalog data is stale; sync GitOps apps to refresh the page state.')
        }
        if (diagnosticsResult.value.freshness.state === 'empty') {
          nextWarnings.push('Project catalog is reachable but empty for the current source.')
        }
      } else {
        setDiagnostics(null)
        nextWarnings.push('Project catalog freshness diagnostics are unavailable.')
      }
      setWarnings(nextWarnings)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load projects'
      setError(message)
      setDiagnostics(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadProjects()
  }, [loadProjects])

  const sortedProjects = useMemo(
    () => [...projects].sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)),
    [projects],
  )
  const catalogByProjectKey = useMemo(() => {
    const map = new Map<string, CatalogJoinRow>()
    for (const row of catalogRows) {
      map.set(`${row.projectId}:${row.env}`, row)
    }
    return map
  }, [catalogRows])
  const mismatchCount =
    (diagnostics?.catalogJoin.projectOnlyCount ?? 0) +
    (diagnostics?.catalogJoin.serviceOnlyCount ?? 0) +
    (diagnostics?.catalogJoin.oneToManyCount ?? 0) +
    (diagnostics?.catalogJoin.ambiguousJoinCount ?? 0)

  return (
    <PageShell title="Projects" description="GitOps-owned application projects and ownership details.">
      <div className="mb-4 rounded-md border border-border bg-card p-4 text-sm text-muted-foreground">
        Projects are sourced from GitOps app definitions. Add or change entries in `workloads/apps/*/envs/*`
        and re-run the catalog sync instead of creating projects in the UI.
      </div>

      {!isLoading ? (
        <div className="mb-4 grid gap-3 md:grid-cols-3">
          <SummaryCard
            label="Catalog source"
            value={
              diagnostics?.freshness.state === 'fresh'
                ? 'Live'
                : diagnostics?.freshness.state === 'warning'
                  ? 'Warning'
                  : diagnostics?.freshness.state === 'stale'
                    ? 'Stale'
                    : diagnostics?.freshness.state === 'empty'
                      ? 'Empty'
                      : 'Unknown'
            }
            meta={`Last sync: ${formatTimestamp(diagnostics?.freshness.lastSyncedAt)}`}
          />
          <SummaryCard
            label="Projects tracked"
            value={String(projects.length)}
            meta={diagnostics ? `Rows in registry: ${diagnostics.freshness.rowCount}` : 'Diagnostics unavailable'}
          />
          <SummaryCard
            label="Join drift"
            value={String(mismatchCount)}
            meta={mismatchCount === 0 ? 'Projects and services are aligned.' : 'Some catalog joins need review.'}
          />
        </div>
      ) : null}

      {!isLoading && diagnostics ? (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-border bg-background p-3 text-sm">
          <span className="text-muted-foreground">Source state</span>
          <SourceStateBadge state={diagnostics.freshness.state} />
          <span className="ml-2 text-muted-foreground">Reconciliation</span>
          <ReconciliationBadge diagnostics={diagnostics.catalogJoin} />
        </div>
      ) : null}

      {!isLoading && warnings.length > 0 ? (
        <div className="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
          <p className="text-sm font-medium text-amber-900 dark:text-amber-200">Partial project readiness</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-900 dark:text-amber-200">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {isLoading ? <LoadingState label="Loading projects..." /> : null}
      {!isLoading && error ? <ErrorState message={error} onRetry={() => void loadProjects()} /> : null}
      {!isLoading && !error && sortedProjects.length === 0 ? (
        <EmptyState
          title="No GitOps projects found."
          description="Sync the project catalog from workloads/apps definitions to populate this page."
        />
      ) : null}
      {!isLoading && !error && sortedProjects.length > 0 ? (
        <ul className="space-y-2 text-sm">
          {sortedProjects.map((project) => (
            <li
              key={project.id}
              id={projectAnchor(project.id, project.environment)}
              className="rounded-md border border-border p-3"
            >
              <p className="font-medium">{project.name}</p>
              <p className="text-muted-foreground">Environment: {project.environment}</p>
              <p className="text-muted-foreground">ID: {project.id}</p>
              {catalogByProjectKey.get(`${project.id}:${project.environment}`)?.primaryServiceId ? (
                <p className="text-muted-foreground">
                  Service:{' '}
                  <AppLink
                    to={`/services/${encodeURIComponent(catalogByProjectKey.get(`${project.id}:${project.environment}`)?.primaryServiceId ?? '')}`}
                    className="text-primary hover:underline"
                  >
                    {catalogByProjectKey.get(`${project.id}:${project.environment}`)?.primaryServiceId}
                  </AppLink>
                </p>
              ) : null}
              {(catalogByProjectKey.get(`${project.id}:${project.environment}`)?.serviceCount ?? 0) > 1 ? (
                <p className="text-muted-foreground">
                  Linked services: {catalogByProjectKey.get(`${project.id}:${project.environment}`)?.serviceIds.join(', ')}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </PageShell>
  )
}
