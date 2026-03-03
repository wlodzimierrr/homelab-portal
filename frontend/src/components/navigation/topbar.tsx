import { Menu } from 'lucide-react'
import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'
import { AppLink } from '@/components/navigation/app-link'

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

export function Topbar({ pathname, theme, onThemeToggle }: TopbarProps) {
  const title = getTitle(pathname)

  return (
    <header className="sticky top-0 z-10 border-b border-border bg-background/90 backdrop-blur">
      <div className="flex h-16 items-center justify-between px-4 md:px-6">
        <div className="flex items-center gap-3">
          <Button asChild variant="ghost" size="icon" className="md:hidden">
            <AppLink to="/dashboard" aria-label="Go to dashboard">
              <Menu className="h-4 w-4" />
            </AppLink>
          </Button>
          <h2 className="text-lg font-semibold">{title}</h2>
        </div>
        <ThemeToggle theme={theme} onToggle={onThemeToggle} />
      </div>
    </header>
  )
}
