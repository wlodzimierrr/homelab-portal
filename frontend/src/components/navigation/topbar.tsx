import { useState } from 'react'
import { Menu, X } from 'lucide-react'
import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'
import { AppLink } from '@/components/navigation/app-link'
import { cn } from '@/lib/utils'

function getTitle(pathname: string) {
  if (pathname.startsWith('/services/') && pathname.endsWith('/deployments')) {
    return 'Service Deployments'
  }
  if (pathname.startsWith('/services/')) {
    return 'Service Details'
  }

  const map: Record<string, string> = {
    '/dashboard': 'Dashboard',
    '/projects': 'Projects',
    '/services': 'Services',
    '/settings': 'Settings',
    '/login': 'Login',
  }
  return map[pathname] ?? 'Portal'
}

interface TopbarProps {
  pathname: string
  theme: 'light' | 'dark'
  onThemeToggle: () => void
}

const mobileLinks = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/projects', label: 'Projects' },
  { to: '/services', label: 'Services' },
  { to: '/settings', label: 'Settings' },
]

export function Topbar({ pathname, theme, onThemeToggle }: TopbarProps) {
  const title = getTitle(pathname)
  const [mobileMenuState, setMobileMenuState] = useState<{ isOpen: boolean; pathname: string }>({
    isOpen: false,
    pathname,
  })
  const isMobileMenuOpen = mobileMenuState.isOpen && mobileMenuState.pathname === pathname

  return (
    <header className="sticky top-0 z-10 border-b border-border bg-background/90 backdrop-blur">
      <div className="flex h-16 items-center justify-between px-4 md:px-6">
        <div className="flex items-center gap-3">
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
          <h2 className="text-lg font-semibold">{title}</h2>
        </div>
        <ThemeToggle theme={theme} onToggle={onThemeToggle} />
      </div>
      {isMobileMenuOpen ? (
        <nav className="border-t border-border bg-background px-4 py-3 md:hidden">
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
