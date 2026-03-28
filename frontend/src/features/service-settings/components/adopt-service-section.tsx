import { Button } from '@/components/ui/button'

interface AdoptServiceSectionProps {
  projectId: string
  setProjectId: (value: string) => void
  submitting: boolean
  error: string
  result: { status: string; message: string; prUrl?: string } | null
  onSubmit: () => void
}

export function AdoptServiceSection({
  projectId,
  setProjectId,
  submitting,
  error,
  result,
  onSubmit,
}: AdoptServiceSectionProps) {
  return (
    <section className="space-y-3 rounded-md border border-border bg-card p-4">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold">Adopt into Project</h2>
        <p className="text-xs text-muted-foreground">
          Link this standalone service to a parent project. This is a metadata-only change (Phase 1 soft-link)
          that creates a PR to add <code>project_id</code> to the service catalog entry.
        </p>
      </div>
      <div className="flex items-end gap-2">
        <label className="flex-1 space-y-1">
          <span className="text-xs font-medium text-muted-foreground">Project ID</span>
          <input
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            placeholder="e.g. my-project"
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
            disabled={submitting}
          />
        </label>
        <Button type="button" onClick={onSubmit} disabled={submitting || !projectId.trim()} variant="outline">
          {submitting ? 'Adopting...' : 'Adopt'}
        </Button>
      </div>
      {error ? <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p> : null}
      {result ? (
        <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
          <p className="text-emerald-900 dark:text-emerald-200">{result.message}</p>
          {result.prUrl ? (
            <a
              href={result.prUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-1 block text-xs text-primary hover:underline"
            >
              View PR
            </a>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
