import {
  getServiceIdFromPath,
  isServiceDeploymentsPath,
  isServiceDetailsPath,
  isServiceSettingsPath,
} from '@/lib/routes'

export type AppRoute =
  | { kind: 'login' }
  | { kind: 'dashboard' }
  | { kind: 'projects' }
  | { kind: 'services' }
  | { kind: 'platform-health' }
  | { kind: 'service-settings'; serviceId: string }
  | { kind: 'service-deployments'; serviceId: string }
  | { kind: 'service-details'; serviceId: string }
  | { kind: 'settings' }

// Routing stays intentionally lightweight, but centralizing pathname matching
// here keeps the shell from accumulating more one-off route checks over time.
export function resolveAppRoute(pathname: string): AppRoute {
  if (pathname === '/login') {
    return { kind: 'login' }
  }
  if (pathname === '/dashboard' || pathname === '/') {
    return { kind: 'dashboard' }
  }
  if (pathname === '/projects') {
    return { kind: 'projects' }
  }
  if (pathname === '/services') {
    return { kind: 'services' }
  }
  if (pathname === '/platform-health') {
    return { kind: 'platform-health' }
  }
  if (isServiceSettingsPath(pathname)) {
    return { kind: 'service-settings', serviceId: getServiceIdFromPath(pathname) }
  }
  if (isServiceDeploymentsPath(pathname)) {
    return { kind: 'service-deployments', serviceId: getServiceIdFromPath(pathname) }
  }
  if (isServiceDetailsPath(pathname)) {
    return { kind: 'service-details', serviceId: getServiceIdFromPath(pathname) }
  }
  if (pathname === '/settings') {
    return { kind: 'settings' }
  }
  return { kind: 'dashboard' }
}
