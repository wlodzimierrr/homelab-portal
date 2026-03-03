import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError } from '@/components/api-error'
import { PageShell } from '@/components/page-shell'
import { Toast } from '@/components/ui/toast'
import { Button } from '@/components/ui/button'
import { createProject, getProjects, type CreateProjectPayload, type Project } from '@/lib/api'

interface ToastState {
  message: string
  variant: 'success' | 'error'
}

function LoadingSkeleton() {
  return (
    <ul className="space-y-2">
      {[1, 2, 3].map((row) => (
        <li key={row} className="rounded-md border border-border p-3">
          <div className="mb-2 h-4 w-40 animate-pulse rounded bg-muted" />
          <div className="h-3 w-52 animate-pulse rounded bg-muted" />
        </li>
      ))}
    </ul>
  )
}

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [toast, setToast] = useState<ToastState | null>(null)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [form, setForm] = useState<CreateProjectPayload>({
    id: '',
    name: '',
    environment: '',
  })

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

  useEffect(() => {
    if (!toast) {
      return
    }

    const timer = window.setTimeout(() => setToast(null), 4500)
    return () => window.clearTimeout(timer)
  }, [toast])

  const sortedProjects = useMemo(
    () => [...projects].sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)),
    [projects],
  )

  const resetCreateForm = () => {
    setForm({ id: '', name: '', environment: '' })
    setCreateError('')
  }

  const openCreateDialog = () => {
    resetCreateForm()
    setIsCreateDialogOpen(true)
  }

  const closeCreateDialog = () => {
    setIsCreateDialogOpen(false)
    setCreateError('')
  }

  const handleCreateProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setCreateError('')
    setIsCreating(true)

    try {
      const createdProject = await createProject(form)

      setProjects((current) => {
        const withoutOld = current.filter((project) => project.id !== createdProject.id)
        return [createdProject, ...withoutOld]
      })

      setToast({ message: `Project "${createdProject.name}" saved.`, variant: 'success' })
      closeCreateDialog()
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to create project'
      setCreateError(message)
      setToast({ message, variant: 'error' })
    } finally {
      setIsCreating(false)
    }
  }

  return (
    <>
      <PageShell title="Projects" description="Application projects and ownership details.">
        <div className="mb-4 flex justify-end">
          <Button type="button" onClick={openCreateDialog}>
            Create project
          </Button>
        </div>

        {isLoading ? <LoadingSkeleton /> : null}
        {!isLoading && error ? <ApiError message={error} onRetry={() => void loadProjects()} /> : null}
        {!isLoading && !error && sortedProjects.length === 0 ? (
          <div className="rounded-md border border-dashed border-border p-6 text-center">
            <p className="text-sm text-muted-foreground">No projects yet. Create your first project.</p>
          </div>
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

      {isCreateDialogOpen ? (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
          <section className="w-full max-w-lg rounded-lg border border-border bg-card p-6 shadow-lg">
            <h2 className="text-lg font-semibold">Create project</h2>
            <p className="mt-1 text-sm text-muted-foreground">Add project metadata for your portal catalog.</p>

            <form className="mt-4 space-y-4" onSubmit={handleCreateProject}>
              <label className="block space-y-1">
                <span className="text-sm">Project ID</span>
                <input
                  required
                  value={form.id}
                  onChange={(event) => setForm((current) => ({ ...current, id: event.target.value }))}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  placeholder="proj-dev"
                />
              </label>
              <label className="block space-y-1">
                <span className="text-sm">Project name</span>
                <input
                  required
                  value={form.name}
                  onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  placeholder="Homelab App"
                />
              </label>
              <label className="block space-y-1">
                <span className="text-sm">Environment</span>
                <input
                  required
                  value={form.environment}
                  onChange={(event) => setForm((current) => ({ ...current, environment: event.target.value }))}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  placeholder="dev"
                />
              </label>

              {createError ? <ApiError message={createError} /> : null}

              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={closeCreateDialog} disabled={isCreating}>
                  Cancel
                </Button>
                <Button type="submit" disabled={isCreating}>
                  {isCreating ? 'Saving...' : 'Save project'}
                </Button>
              </div>
            </form>
          </section>
        </div>
      ) : null}

      {toast ? <Toast message={toast.message} variant={toast.variant} onClose={() => setToast(null)} /> : null}
    </>
  )
}
