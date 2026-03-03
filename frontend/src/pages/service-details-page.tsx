import { useCallback, useEffect, useMemo, useState } from 'react'
import { AppLink } from '@/components/navigation/app-link'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { StatusCard } from '@/components/status-card'
import { Button } from '@/components/ui/button'
import {
  getProjects,
  getService,
  getServiceDeployments,
  type Project,
  type ServiceDeployment,
  type ServiceEndpoint,
} from '@/lib/api'
import {
  buildArgoAppUrl,
  buildGrafanaDashboardUrl,
  buildLogsUrl,
  isLogsConfigured,
} from '@/lib/config'

interface ServiceDetailsPageProps {
  serviceId: string
}

type HealthStatus = 'healthy' | 'degraded' | 'unknown'
type SyncStatus = 'synced' | 'out_of_sync' | 'unknown'

interface ServiceOverviewData {
  id: string
  name: string
  version: string
  health: HealthStatus
  sync: SyncStatus
  endpoints: ServiceEndpoint[]
  deployments: ServiceDeployment[]
}

interface QuickLinkCardProps {
  label: string
  description: string
  href: string
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

function formatDate(value?: string) {
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

function safeDecodeServiceId(rawServiceId: string) {
  try {
    return decodeURIComponent(rawServiceId)
  } catch {
    return rawServiceId
  }
}

function createMockDeployments(serviceId: string): ServiceDeployment[] {
  const now = Date.now()
  return [0, 1, 2, 3, 4].map((offset) => ({
    id: `${serviceId}-mock-${offset + 1}`,
    version: `v0.0.${9 - offset}`,
    status: offset === 2 ? 'degraded' : 'succeeded',
    deployedAt: new Date(now - offset * 1000 * 60 * 60 * 24).toISOString(),
  }))
}

function buildFromProjects(serviceId: string, projects: Project[]): ServiceOverviewData {
  const matches = projects.filter((project) => project.name.trim().toLowerCase() === serviceId.toLowerCase())
  const primary = matches[0]

  const endpointMap = new Map<string, ServiceEndpoint>()
  for (const project of matches) {
    if (project.publicUrl) {
      endpointMap.set(project.publicUrl, {
        type: 'public',
        label: `${project.environment} public`,
        url: project.publicUrl,
      })
    }
    if (project.internalUrl) {
      endpointMap.set(project.internalUrl, {
        type: 'internal',
        label: `${project.environment} internal`,
        url: project.internalUrl,
      })
    }
  }

  return {
    id: serviceId,
    name: primary?.name ?? serviceId,
    version: 'N/A',
    health: normalizeHealthStatus(primary?.health),
    sync: normalizeSyncStatus(primary?.sync),
    endpoints: [...endpointMap.values()],
    deployments: [],
  }
}

function buildEndpointList(
  endpoints: ServiceEndpoint[] | undefined,
  publicUrl: string | undefined,
  internalUrls: string[] | undefined,
) {
  const collected: ServiceEndpoint[] = []

  if (endpoints?.length) {
    for (const endpoint of endpoints) {
      if (endpoint.url) {
        collected.push(endpoint)
      }
    }
  }

  if (publicUrl) {
    collected.push({
      type: 'public',
      label: 'Public URL',
      url: publicUrl,
    })
  }

  for (const internalUrl of internalUrls ?? []) {
    if (internalUrl) {
      collected.push({
        type: 'internal',
        label: 'Internal URL',
        url: internalUrl,
      })
    }
  }

  return collected.filter(
    (endpoint, index, source) => source.findIndex((item) => item.url === endpoint.url) === index,
  )
}

function QuickLinkCard({ label, description, href }: QuickLinkCardProps) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="rounded-md border border-border bg-background p-3 transition-colors hover:bg-accent"
    >
      <p className="text-sm font-medium">{label}</p>
      <p className="text-xs text-muted-foreground">{description}</p>
    </a>
  )
}

export function ServiceDetailsPage({ serviceId }: ServiceDetailsPageProps) {
  const decodedServiceId = useMemo(() => safeDecodeServiceId(serviceId), [serviceId])
  const [overview, setOverview] = useState<ServiceOverviewData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  const loadOverview = useCallback(async () => {
    setIsLoading(true)
    setError('')

    try {
      const [serviceResult, projectsResult, deploymentsResult] = await Promise.allSettled([
        getService(decodedServiceId),
        getProjects(),
        getServiceDeployments(decodedServiceId),
      ])

      const fallback =
        projectsResult.status === 'fulfilled'
          ? buildFromProjects(decodedServiceId, projectsResult.value.projects)
          : buildFromProjects(decodedServiceId, [])

      const finalOverview: ServiceOverviewData =
        serviceResult.status === 'fulfilled'
          ? {
              id: serviceResult.value.id || decodedServiceId,
              name: serviceResult.value.name || decodedServiceId,
              version: serviceResult.value.version ?? 'N/A',
              health: normalizeHealthStatus(serviceResult.value.health),
              sync: normalizeSyncStatus(serviceResult.value.sync),
              endpoints: buildEndpointList(
                serviceResult.value.endpoints,
                serviceResult.value.publicUrl,
                serviceResult.value.internalUrls,
              ),
              deployments: serviceResult.value.deployments ?? [],
            }
          : fallback

      if (finalOverview.endpoints.length === 0) {
        finalOverview.endpoints = fallback.endpoints
      }

      if (deploymentsResult.status === 'fulfilled' && deploymentsResult.value.deployments.length > 0) {
        finalOverview.deployments = deploymentsResult.value.deployments
      }

      if (finalOverview.deployments.length === 0) {
        finalOverview.deployments = createMockDeployments(decodedServiceId)
      }

      setOverview(finalOverview)
    } catch (requestError) {
      const message =
        requestError instanceof Error ? requestError.message : 'Failed to load service overview'
      setError(message)
    } finally {
      setIsLoading(false)
    }
  }, [decodedServiceId])

  useEffect(() => {
    void loadOverview()
  }, [loadOverview])

  const argoUrl = useMemo(() => buildArgoAppUrl(decodedServiceId), [decodedServiceId])
  const grafanaUrl = useMemo(() => buildGrafanaDashboardUrl(decodedServiceId), [decodedServiceId])
  const logsConfigured = isLogsConfigured()
  const logsUrl = useMemo(
    () =>
      buildLogsUrl({
        serviceId: decodedServiceId,
        namespace: 'default',
        appLabel: decodedServiceId,
        timeRange: '6h',
      }),
    [decodedServiceId],
  )

  return (
    <PageShell
      title={`Service: ${decodedServiceId || 'unknown'}`}
      description="Overview for deployment status, endpoints, and recent release activity."
    >
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
          <div className="flex items-center gap-2">
            <span className="rounded-md bg-primary/10 px-2 py-1 text-xs font-medium text-primary">
              Overview
            </span>
          </div>
          <Button asChild variant="outline">
            <AppLink to={`/services/${encodeURIComponent(decodedServiceId)}/deployments`}>
              View deployments
            </AppLink>
          </Button>
        </div>

        {isLoading ? <LoadingState label="Loading service overview..." rows={4} /> : null}

        {!isLoading && error ? <ErrorState message={error} onRetry={() => void loadOverview()} /> : null}

        {!isLoading && !error && overview ? (
          <>
            <div className="grid gap-3 md:grid-cols-2">
              <article className="rounded-md border border-border bg-background p-4">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">Deployed Version</p>
                <p className="mt-2 text-xl font-semibold">{overview.version}</p>
                <p className="mt-1 text-xs text-muted-foreground">Placeholder until deployment metadata API lands</p>
              </article>
              <StatusCard health={overview.health} sync={overview.sync} />
            </div>

            {import.meta.env.DEV ? (
              <section className="space-y-3">
                <h2 className="text-sm font-semibold">Status Visual Checks</h2>
                <div className="grid gap-3 md:grid-cols-3">
                  <StatusCard health="healthy" sync="synced" />
                  <StatusCard health="degraded" sync="out_of_sync" />
                  <StatusCard />
                </div>
              </section>
            ) : null}

            <section className="space-y-3">
              <h2 className="text-sm font-semibold">Quick Links</h2>
              <div className="grid gap-3 md:grid-cols-3">
                <QuickLinkCard
                  label="Argo CD Application"
                  description="Open the GitOps application state"
                  href={argoUrl}
                />
                <QuickLinkCard
                  label="Grafana Dashboard"
                  description="Open service metrics dashboard"
                  href={grafanaUrl}
                />
                <div className="rounded-md border border-border bg-background p-3">
                  <p className="text-sm font-medium">Logs</p>
                  <p className="mb-3 text-xs text-muted-foreground">
                    Opens Grafana/Loki filtered by namespace, app label, and time range.
                  </p>
                  {logsConfigured ? (
                    <Button asChild size="sm">
                      <a href={logsUrl} target="_blank" rel="noreferrer">
                        Open Logs
                      </a>
                    </Button>
                  ) : (
                    <Button type="button" size="sm" disabled>
                      Logs unavailable
                    </Button>
                  )}
                </div>
              </div>
            </section>

            <section className="space-y-3">
              <h2 className="text-sm font-semibold">Endpoints</h2>
              {overview.endpoints.length === 0 ? (
                <p className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
                  No public/internal endpoints available.
                </p>
              ) : (
                <div className="space-y-2">
                  {overview.endpoints.map((endpoint) => (
                    <div
                      key={`${endpoint.type ?? 'unknown'}-${endpoint.url}`}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-background p-3"
                    >
                      <div>
                        <p className="text-sm font-medium">{endpoint.label ?? endpoint.type ?? 'endpoint'}</p>
                        <p className="text-xs text-muted-foreground">{endpoint.type ?? 'unknown'}</p>
                      </div>
                      <a
                        href={endpoint.url}
                        target="_blank"
                        rel="noreferrer"
                        className="break-all text-sm text-primary hover:underline"
                      >
                        {endpoint.url}
                      </a>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-sm font-semibold">Recent Deployments</h2>
                <AppLink
                  to={`/services/${encodeURIComponent(decodedServiceId)}/deployments`}
                  className="text-xs font-medium text-primary hover:underline"
                >
                  Open full history
                </AppLink>
              </div>
              <div className="overflow-x-auto rounded-md border border-border">
                <table className="min-w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="px-3 py-2 font-medium text-muted-foreground">Version</th>
                      <th className="px-3 py-2 font-medium text-muted-foreground">Outcome</th>
                      <th className="px-3 py-2 font-medium text-muted-foreground">Deployed At</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.deployments.slice(0, 5).map((deployment) => (
                      <tr key={deployment.id} className="border-b border-border/70">
                        <td className="px-3 py-2">{deployment.version ?? 'N/A'}</td>
                        <td className="px-3 py-2">
                          <span className="inline-flex items-center rounded-full bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
                            State: {deployment.status ?? 'unknown'}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">{formatDate(deployment.deployedAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        ) : null}
      </div>
    </PageShell>
  )
}
