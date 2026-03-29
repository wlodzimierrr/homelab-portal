import { AppLink } from '@/components/navigation/app-link'
import type { ServiceProjectContext } from '@/lib/api/catalog'

interface ServiceGroupingContextProps {
  projectContext: ServiceProjectContext
  serviceEnv?: string
}

export function ServiceGroupingContext({
  projectContext,
  serviceEnv,
}: ServiceGroupingContextProps) {
  const diagnosticsLink =
    projectContext.projectId
      ? `/projects#${encodeURIComponent(`${projectContext.projectId}-${serviceEnv || 'dev'}`)}`
      : null

  return (
    <article className="rounded-md border border-border bg-background p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Service Group</p>
          <p className="text-sm text-muted-foreground">
            This service belongs to the namespace and related-service group shown here.
          </p>
        </div>
        {diagnosticsLink ? (
          <AppLink to={diagnosticsLink} className="text-xs text-muted-foreground hover:text-primary hover:underline">
            View project diagnostics
          </AppLink>
        ) : null}
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]">
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Linked project</p>
          <p className="text-sm font-medium text-foreground">
            {projectContext.projectName || projectContext.projectId || 'Linked project'}
          </p>
        </div>
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Namespace</p>
          <p className="font-mono text-sm text-foreground">{projectContext.namespace}</p>
        </div>
      </div>

      {projectContext.siblingServiceIds.length > 0 ? (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Related services</p>
          <p className="text-xs text-muted-foreground">
            These services share the same linked project and namespace context.
          </p>
          <div className="flex flex-wrap gap-2">
            {projectContext.siblingServiceIds.map((sid) => (
              <AppLink
                key={sid}
                to={`/services/${encodeURIComponent(sid)}`}
                className="rounded-full border border-border px-2 py-1 text-xs text-primary hover:underline"
              >
                {sid}
              </AppLink>
            ))}
          </div>
        </div>
      ) : null}
    </article>
  )
}
