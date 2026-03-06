import { useCallback, useEffect, useMemo, useState } from 'react'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { getProjects, type Project } from '@/lib/api'

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  const loadProjects = useCallback(async () => {
    setIsLoading(true)
    setError('')
    try {
      const response = await getProjects()
      setProjects(response.projects)
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
            <li key={project.id} className="rounded-md border border-border p-3">
              <p className="font-medium">{project.name}</p>
              <p className="text-muted-foreground">Environment: {project.environment}</p>
              <p className="text-muted-foreground">ID: {project.id}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </PageShell>
  )
}
