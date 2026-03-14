import type { ReactNode } from 'react'

interface PageShellProps {
  title: string
  description: string
  action?: ReactNode
  children?: ReactNode
}

export function PageShell({ title, description, action, children }: PageShellProps) {
  return (
    <section className="w-full max-w-[1600px] space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{title}</h1>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </header>
      <div className="rounded-lg border border-border bg-card p-6">{children}</div>
    </section>
  )
}
