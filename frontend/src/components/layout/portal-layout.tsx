import { PortalSidebar } from '@/components/navigation/portal-sidebar'
import { Topbar } from '@/components/navigation/topbar'
import type { ReactNode } from 'react'
import type { IncidentSeverity } from '@/lib/incident-alerts'

interface PortalLayoutProps {
  children: ReactNode
  pathname: string
  theme: 'light' | 'dark'
  onThemeToggle: () => void
  showIncidentBanner: boolean
  incidentActiveCount: number
  incidentHighestSeverity: IncidentSeverity | null
  onIncidentDismiss: () => void
}

export function PortalLayout({
  children,
  pathname,
  theme,
  onThemeToggle,
  showIncidentBanner,
  incidentActiveCount,
  incidentHighestSeverity,
  onIncidentDismiss,
}: PortalLayoutProps) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex max-w-screen-2xl">
        <PortalSidebar pathname={pathname} theme={theme} onThemeToggle={onThemeToggle} />
        <div className="flex min-h-screen flex-1 flex-col">
          <Topbar
            pathname={pathname}
            theme={theme}
            onThemeToggle={onThemeToggle}
            showIncidentBanner={showIncidentBanner}
            incidentActiveCount={incidentActiveCount}
            incidentHighestSeverity={incidentHighestSeverity}
            onIncidentDismiss={onIncidentDismiss}
          />
          <main className="flex-1 p-4 md:p-6">
            {children}
          </main>
        </div>
      </div>
    </div>
  )
}
