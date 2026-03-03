import { FolderKanban, LayoutDashboard, Server, Settings } from 'lucide-react'
import { cn } from '@/lib/utils'
import { AppLink } from '@/components/navigation/app-link'

const links = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/projects', label: 'Projects', icon: FolderKanban },
  { to: '/services', label: 'Services', icon: Server },
  { to: '/settings', label: 'Settings', icon: Settings },
]

interface PortalSidebarProps {
  pathname: string
}

export function PortalSidebar({ pathname }: PortalSidebarProps) {
  return (
    <aside className="sticky top-0 hidden h-screen w-64 shrink-0 border-r border-border bg-sidebar p-4 md:block">
      <div className="mb-6">
        <p className="text-sm text-muted-foreground">Center of Homelab</p>
        <h1 className="text-lg font-semibold">Homelab Control</h1>
      </div>
      <nav className="space-y-1">
        {links.map(({ to, label, icon: Icon }) => (
          <AppLink
            key={to}
            to={to}
            className={cn(
              'flex items-center gap-2 rounded-md px-3 py-2 text-sm text-sidebar-foreground transition-colors',
              pathname === to || pathname.startsWith(`${to}/`)
                ? 'bg-sidebar-accent font-medium'
                : 'hover:bg-sidebar-accent/60',
            )}
          >
            <Icon className="h-4 w-4" />
            <span>{label}</span>
          </AppLink>
        ))}
      </nav>
    </aside>
  )
}
