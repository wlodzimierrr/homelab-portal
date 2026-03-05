import { useState } from 'react'
import { Menu, X } from 'lucide-react'
import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'
import { AppLink } from '@/components/navigation/app-link'
import type { IncidentSeverity } from '@/lib/incident-alerts'
import { cn } from '@/lib/utils'

interface TopbarProps {
  pathname: string
  theme: 'light' | 'dark'
  onThemeToggle: () => void
  showIncidentBanner: boolean
  incidentActiveCount: number
  incidentHighestSeverity: IncidentSeverity | null
  onIncidentDismiss: () => void
}

const mobileLinks = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/projects', label: 'Projects' },
  { to: '/services', label: 'Services' },
  { to: '/platform-health', label: 'Platform Health' },
  { to: '/settings', label: 'Settings' },
]

function getIncidentTone(severity: IncidentSeverity | null) {
  if (severity === 'critical') {
    return 'border-rose-500/60 bg-rose-500/10 text-rose-800 dark:text-rose-200'
  }
  if (severity === 'warning') {
    return 'border-amber-500/60 bg-amber-500/10 text-amber-800 dark:text-amber-200'
  }
  return 'border-sky-500/60 bg-sky-500/10 text-sky-800 dark:text-sky-200'
}

export function Topbar({
  pathname,
  theme,
  onThemeToggle,
  showIncidentBanner,
  incidentActiveCount,
  incidentHighestSeverity,
  onIncidentDismiss,
}: TopbarProps) {
  const [mobileMenuState, setMobileMenuState] = useState<{ isOpen: boolean; pathname: string }>({
    isOpen: false,
    pathname,
  })
  const isMobileMenuOpen = mobileMenuState.isOpen && mobileMenuState.pathname === pathname

  return (
    <header className="sticky top-0 z-10 bg-background/90 backdrop-blur">
      <div className="flex h-14 items-center justify-between gap-2 px-4 md:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="md:hidden"
            aria-label={isMobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'}
            onClick={() => {
              setMobileMenuState({
                isOpen: !isMobileMenuOpen,
                pathname,
              })
            }}
          >
            {isMobileMenuOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </Button>
          {showIncidentBanner ? (
            <div
              className={cn(
                'flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md border px-2 py-1 text-xs',
                getIncidentTone(incidentHighestSeverity),
              )}
            >
              <span className="truncate font-medium">Active incidents: {incidentActiveCount}</span>
              {incidentHighestSeverity !== 'critical' ? (
                <button
                  type="button"
                  className="shrink-0 underline underline-offset-2"
                  onClick={onIncidentDismiss}
                >
                  Dismiss
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="md:hidden">
          <ThemeToggle theme={theme} onToggle={onThemeToggle} />
        </div>
      </div>
      {isMobileMenuOpen ? (
        <nav className="bg-background px-4 py-3 md:hidden">
          <ul className="space-y-1">
            {mobileLinks.map((link) => (
              <li key={link.to}>
                <AppLink
                  to={link.to}
                  className={cn(
                    'block rounded-md px-3 py-2 text-sm',
                    pathname === link.to || pathname.startsWith(`${link.to}/`)
                      ? 'bg-accent font-medium'
                      : 'text-muted-foreground hover:bg-accent/70 hover:text-foreground',
                  )}
                  onClick={() =>
                    setMobileMenuState({
                      isOpen: false,
                      pathname,
                    })
                  }
                >
                  {link.label}
                </AppLink>
              </li>
            ))}
          </ul>
        </nav>
      ) : null}
    </header>
  )
}
