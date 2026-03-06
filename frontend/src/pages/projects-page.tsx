import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { getCatalogReconciliation, getProjects, type CatalogJoinRow, type Project } from '@/lib/api'

function projectAnchor(projectId: string, env: string) {
  return `${projectId}-${env}`.toLowerCase()
}

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [catalogRows, setCatalogRows] = useState<CatalogJoinRow[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  const loadProjects = useCallback(async () => {
    setIsLoading(true)
    setError('')
    try {
      const [projectsResult, catalogResult] = await Promise.allSettled([getProjects(), getCatalogReconciliation()])
      if (projectsResult.status !== 'fulfilled') {
        throw projectsResult.reason
      }

      setProjects(projectsResult.value.projects)
      setCatalogRows(catalogResult.status === 'fulfilled' ? catalogResult.value.rows : [])
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load projects'
      setError(message)
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

  return (
    <PageShell title="Projects" description="GitOps-owned application projects and ownership details.">
      <div className="mb-4 rounded-md border border-border bg-card p-4 text-sm text-muted-foreground">
        Projects are sourced from GitOps app definitions. Add or change entries in `workloads/apps/*/envs/*`
        and re-run the catalog sync instead of creating projects in the UI.
      </div>

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
