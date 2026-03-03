import type { ReactNode } from 'react'

interface PageShellProps {
  title: string
  description: string
  children?: ReactNode
}

export function PageShell({ title, description, children }: PageShellProps) {
  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">{title}</h1>
        <p className="text-sm text-muted-foreground">{description}</p>
      </header>
      <div className="rounded-lg border border-border bg-card p-6">{children}</div>
    </section>
  )
}
