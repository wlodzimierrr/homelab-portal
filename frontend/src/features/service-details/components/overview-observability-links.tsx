interface ExternalObservabilityLink {
  href?: string
  reason?: string | null
}

interface OverviewObservabilityLinksProps {
  argoUrl?: string
  grafanaDashboardLink: ExternalObservabilityLink
  latencyPanelLink: ExternalObservabilityLink
  errorPanelLink: ExternalObservabilityLink
  logsLink: ExternalObservabilityLink
}

interface QuickLinkCardProps {
  label: string
  description: string
  href?: string
  unavailableMessage?: string
}

function QuickLinkCard({ label, description, href, unavailableMessage }: QuickLinkCardProps) {
  if (!href || href.trim() === '') {
    return (
      <div className="rounded-md border border-border bg-background p-3">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
        <p className="mt-2 text-xs text-muted-foreground">
          {unavailableMessage ?? 'Unavailable due to missing URL configuration.'}
        </p>
      </div>
    )
  }

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

function formatGrafanaLinkUnavailable(reason: string | null | undefined) {
  return reason ?? 'Unavailable because the Grafana URL template could not be resolved for this service.'
}

function formatLogsLinkUnavailable(reason: string | null | undefined) {
  return reason ?? 'Unavailable because the Grafana/Loki URL template could not be resolved for this service.'
}

export function OverviewObservabilityLinks({
  argoUrl,
  grafanaDashboardLink,
  latencyPanelLink,
  errorPanelLink,
  logsLink,
}: OverviewObservabilityLinksProps) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold">External Tools</h2>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <QuickLinkCard
          label="Argo CD Application"
          description="Open the GitOps application state"
          href={argoUrl}
          unavailableMessage="Unavailable because the Argo CD base URL or application path template is not configured."
        />
        <QuickLinkCard
          label="Grafana Dashboard"
          description="Open the service metrics dashboard"
          href={grafanaDashboardLink.href}
          unavailableMessage={formatGrafanaLinkUnavailable(grafanaDashboardLink.reason)}
        />
        <QuickLinkCard
          label="Latency Panel"
          description="Inspect latency trends in Grafana"
          href={latencyPanelLink.href}
          unavailableMessage={formatGrafanaLinkUnavailable(latencyPanelLink.reason)}
        />
        <QuickLinkCard
          label="Error Panel"
          description="Inspect error-rate trends in Grafana"
          href={errorPanelLink.href}
          unavailableMessage={formatGrafanaLinkUnavailable(errorPanelLink.reason)}
        />
        <QuickLinkCard
          label="Logs"
          description="Open full service logs in Grafana or Loki"
          href={logsLink.href}
          unavailableMessage={formatLogsLinkUnavailable(logsLink.reason)}
        />
      </div>
    </section>
  )
}
