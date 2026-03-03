import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError } from '@/components/api-error'
import { PageShell } from '@/components/page-shell'
import { AppLink } from '@/components/navigation/app-link'
import { getProjects, type Project } from '@/lib/api'
import { cn } from '@/lib/utils'

type HealthStatus = 'healthy' | 'degraded' | 'unknown'
type SyncStatus = 'synced' | 'out_of_sync' | 'unknown'

interface ServiceRow {
  id: string
  name: string
  environments: string[]
  health: HealthStatus
  sync: SyncStatus
  publicUrl?: string
  lastDeployAt?: string
}

function normalizeHealthStatus(value?: string): HealthStatus {
  if (!value) {
    return 'unknown'
  }

  const normalized = value.trim().toLowerCase()
  if (normalized === 'healthy') {
    return 'healthy'
  }
  if (normalized === 'degraded' || normalized === 'unhealthy') {
    return 'degraded'
  }
  return 'unknown'
}

function normalizeSyncStatus(value?: string): SyncStatus {
  if (!value) {
    return 'unknown'
  }

  const normalized = value.trim().toLowerCase()
  if (normalized === 'synced') {
    return 'synced'
  }
  if (normalized === 'out_of_sync' || normalized === 'out-of-sync' || normalized === 'outofsync') {
    return 'out_of_sync'
  }
  return 'unknown'
}

function formatLastDeploy(value?: string) {
  if (!value) {
    return 'N/A'
  }

  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return 'N/A'
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)
}

function adaptProjectsToServices(projects: Project[]): ServiceRow[] {
  const grouped = new Map<string, ServiceRow>()

  for (const project of projects) {
    const key = project.name.trim().toLowerCase()
    const current = grouped.get(key)

    const nextHealth = normalizeHealthStatus(project.health)
    const nextSync = normalizeSyncStatus(project.sync)

    if (!current) {
      grouped.set(key, {
        id: project.name,
        name: project.name,
        environments: [project.environment],
        health: nextHealth,
        sync: nextSync,
        publicUrl: project.publicUrl,
        lastDeployAt: project.lastDeployAt,
      })
      continue
    }

    if (!current.environments.includes(project.environment)) {
      current.environments.push(project.environment)
      current.environments.sort((a, b) => a.localeCompare(b))
    }

    if (current.health === 'unknown' && nextHealth !== 'unknown') {
      current.health = nextHealth
    }

    if (current.sync === 'unknown' && nextSync !== 'unknown') {
      current.sync = nextSync
    }

    if (!current.publicUrl && project.publicUrl) {
      current.publicUrl = project.publicUrl
    }

    if (!current.lastDeployAt && project.lastDeployAt) {
      current.lastDeployAt = project.lastDeployAt
    }
  }

  return [...grouped.values()].sort((a, b) => a.name.localeCompare(b.name))
}

function LoadingSkeleton() {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border">
            {['Service', 'Environment(s)', 'Status', 'Public URL', 'Last Deploy'].map((column) => (
              <th key={column} className="px-3 py-3 font-medium text-muted-foreground">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[1, 2, 3].map((row) => (
            <tr key={row} className="border-b border-border/70">
              <td className="px-3 py-3">
                <div className="h-4 w-36 animate-pulse rounded bg-muted" />
              </td>
              <td className="px-3 py-3">
                <div className="h-4 w-24 animate-pulse rounded bg-muted" />
              </td>
              <td className="px-3 py-3">
                <div className="h-6 w-32 animate-pulse rounded bg-muted" />
              </td>
              <td className="px-3 py-3">
                <div className="h-4 w-44 animate-pulse rounded bg-muted" />
              </td>
              <td className="px-3 py-3">
                <div className="h-4 w-36 animate-pulse rounded bg-muted" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function StatusBadge({ label, value }: { label: string; value: string }) {
  const normalized = value.toLowerCase()
  const tone =
    normalized === 'healthy' || normalized === 'synced'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : normalized === 'degraded' || normalized === 'out_of_sync'
        ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
        : 'bg-muted text-muted-foreground'

  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', tone)}>
      {label}: {value}
    </span>
  )
}

export function ServicesPage() {
  const [services, setServices] = useState<ServiceRow[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [environmentFilter, setEnvironmentFilter] = useState('all')

  const loadServices = useCallback(async () => {
    setIsLoading(true)
    setError('')
    try {
      const response = await getProjects()
      setServices(adaptProjectsToServices(response.projects))
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load services'
      setError(message)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadServices()
  }, [loadServices])

  const environmentOptions = useMemo(() => {
    const unique = new Set<string>()
    for (const service of services) {
      for (const env of service.environments) {
        unique.add(env)
      }
    }
    return [...unique].sort((a, b) => a.localeCompare(b))
  }, [services])

  const filteredServices = useMemo(() => {
    const query = search.trim().toLowerCase()

    return services.filter((service) => {
      const matchesEnvironment =
        environmentFilter === 'all' || service.environments.includes(environmentFilter)

      if (!matchesEnvironment) {
        return false
      }

      if (!query) {
        return true
      }

      const searchable = [
        service.name,
        service.publicUrl ?? '',
        service.environments.join(' '),
        service.health,
        service.sync,
      ]
        .join(' ')
        .toLowerCase()

      return searchable.includes(query)
    })
  }, [environmentFilter, search, services])

  return (
    <PageShell
      title="Services"
      description="Read-only service catalog with environments, runtime status, and external links."
    >
      <div className="mb-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_220px]">
        <label className="space-y-1">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Search</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search service, env, or status..."
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Environment</span>
          <select
            value={environmentFilter}
            onChange={(event) => setEnvironmentFilter(event.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          >
            <option value="all">All environments</option>
            {environmentOptions.map((env) => (
              <option key={env} value={env}>
                {env}
              </option>
            ))}
          </select>
        </label>
      </div>

      {isLoading ? <LoadingSkeleton /> : null}
      {!isLoading && error ? <ApiError message={error} onRetry={() => void loadServices()} /> : null}
      {!isLoading && !error && services.length === 0 ? (
        <div className="rounded-md border border-dashed border-border p-6 text-center">
          <p className="text-sm text-muted-foreground">No services available yet.</p>
        </div>
      ) : null}
      {!isLoading && !error && services.length > 0 && filteredServices.length === 0 ? (
        <div className="rounded-md border border-dashed border-border p-6 text-center">
          <p className="text-sm text-muted-foreground">No services match the current search/filter.</p>
        </div>
      ) : null}
      {!isLoading && !error && filteredServices.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="px-3 py-3 font-medium text-muted-foreground">Service</th>
                <th className="px-3 py-3 font-medium text-muted-foreground">Environment(s)</th>
                <th className="px-3 py-3 font-medium text-muted-foreground">Status</th>
                <th className="px-3 py-3 font-medium text-muted-foreground">Public URL</th>
                <th className="px-3 py-3 font-medium text-muted-foreground">Last Deploy</th>
              </tr>
            </thead>
            <tbody>
              {filteredServices.map((service) => (
                <tr key={service.id} className="border-b border-border/70 align-top">
                  <td className="px-3 py-3 font-medium">
                    <AppLink to={`/services/${encodeURIComponent(service.id)}`} className="hover:underline">
                      {service.name}
                    </AppLink>
                  </td>
                  <td className="px-3 py-3 text-muted-foreground">{service.environments.join(', ')}</td>
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap gap-2">
                      <StatusBadge label="Health" value={service.health} />
                      <StatusBadge label="Sync" value={service.sync} />
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    {service.publicUrl ? (
                      <a
                        href={service.publicUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="break-all text-primary hover:underline"
                      >
                        {service.publicUrl}
                      </a>
                    ) : (
                      <span className="text-muted-foreground">N/A</span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-muted-foreground">{formatLastDeploy(service.lastDeployAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </PageShell>
  )
}
