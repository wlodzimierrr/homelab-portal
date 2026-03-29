import { HeartPulse, LayoutDashboard, Server, Settings } from 'lucide-react'

export const primaryNavLinks = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/services', label: 'Services', icon: Server },
  { to: '/platform-health', label: 'Platform Health', icon: HeartPulse },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export const primaryMobileNavLinks = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/services', label: 'Services' },
  { to: '/platform-health', label: 'Platform Health' },
  { to: '/settings', label: 'Settings' },
]
