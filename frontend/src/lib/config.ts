const env = import.meta.env ?? {}
const VALID_AUTH_MODES = new Set(['bearer_token', 'forwarded_identity'] as const)
export type AuthMode = 'bearer_token' | 'forwarded_identity'

const TEMPLATE_TOKEN_REGEX = /\{\{\s*([a-zA-Z0-9_]+)\s*\}\}|\{([a-zA-Z0-9_]+)\}/g

interface MonitoringUrlOptions {
  baseUrl: string
  pathTemplate: string
  values: Record<string, string | undefined>
  context: string
  fallbackPath?: string
}

export interface MonitoringUrlResult {
  href: string
  reason: string | null
  missingVariables: string[]
}

function warnTemplate(context: string, message: string) {
  if (import.meta.env?.DEV) {
    console.warn(`[monitoring-url] ${context}: ${message}`)
  }
}

function warnAuthMode(message: string) {
  if (import.meta.env?.DEV) {
    console.warn(`[auth-mode] ${message}`)
  }
}

export function getAuthMode(): AuthMode {
  const runtimeMode =
    typeof window !== 'undefined'
      ? (window as Window & { __PORTAL_AUTH_MODE__?: string }).__PORTAL_AUTH_MODE__
      : undefined
  const rawMode = String(runtimeMode ?? env.VITE_AUTH_MODE ?? 'bearer_token').trim().toLowerCase()

  if (VALID_AUTH_MODES.has(rawMode as AuthMode)) {
    return rawMode as AuthMode
  }

  warnAuthMode(`invalid auth mode "${rawMode}", falling back to bearer_token`)
  return 'bearer_token'
}

function fillTemplate(template: string, values: Record<string, string | undefined>, context: string) {
  const missing = new Set<string>()

  const rendered = template.replace(TEMPLATE_TOKEN_REGEX, (_match, mustacheKey, braceKey) => {
    const key = (mustacheKey ?? braceKey) as string
    const value = values[key]

    if (typeof value !== 'string' || value.trim() === '') {
      missing.add(key)
      return ''
    }

    return encodeURIComponent(value)
  })

  if (missing.size > 0) {
    warnTemplate(context, `missing template variables: ${[...missing].sort().join(', ')}`)
  }

  return {
    rendered,
    missingVariables: [...missing].sort(),
  }
}

function joinUrl(baseUrl: string, path: string) {
  if (/^https?:\/\//i.test(path)) {
    return path
  }

  if (!baseUrl) {
    return ''
  }

  const normalizedBase = baseUrl.replace(/\/+$/, '')
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${normalizedBase}${normalizedPath}`
}

function normalizePath(path: string, context: string, fallbackPath: string) {
  const trimmed = path.trim()

  if (!trimmed) {
    warnTemplate(context, `empty rendered path, falling back to ${fallbackPath}`)
    return fallbackPath
  }

  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed
  }

  if (trimmed.includes('{') || trimmed.includes('}')) {
    warnTemplate(context, `unresolved template markers detected, falling back to ${fallbackPath}`)
    return fallbackPath
  }

  return trimmed.startsWith('/') ? trimmed : `/${trimmed}`
}

function readPositiveNumber(value: string | undefined, fallback: number) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback
  }
  return parsed
}

// When the portal is served from the homelab domains, provide sensible defaults
// for deep links so local development does not need every URL configured by hand.
function inferHomelabFrontendDefaults() {
  if (typeof window === 'undefined') {
    return {
      argoBaseUrl: '',
      grafanaBaseUrl: '',
    }
  }

  const hostname = window.location.hostname.toLowerCase()
  const isHomelabHost =
    hostname.endsWith('.homelab.local') ||
    hostname.endsWith('.wlodzimierrr.co.uk')

  if (!isHomelabHost) {
    return {
      argoBaseUrl: '',
      grafanaBaseUrl: '',
    }
  }

  return {
    argoBaseUrl: 'https://argocd.wlodzimierrr.co.uk',
    grafanaBaseUrl: 'http://monitoring.homelab.local',
  }
}

const inferredHomelabDefaults = inferHomelabFrontendDefaults()

export function buildMonitoringUrl({
  baseUrl,
  pathTemplate,
  values,
  context,
  fallbackPath = '/',
}: MonitoringUrlOptions) {
  // This variant always returns a usable URL by falling back when templates are
  // incomplete. It is used where a degraded deep link is still better than none.
  const renderedPath = fillTemplate(pathTemplate, values, context).rendered
  const safePath = normalizePath(renderedPath, context, fallbackPath)

  if (!baseUrl && !/^https?:\/\//i.test(safePath)) {
    warnTemplate(context, 'base URL is empty; returning disabled URL')
    return ''
  }

  return joinUrl(baseUrl, safePath)
}

function buildMonitoringLink({
  baseUrl,
  pathTemplate,
  values,
  context,
  fallbackPath = '/',
}: MonitoringUrlOptions): MonitoringUrlResult {
  const trimmedTemplate = pathTemplate.trim()

  if (!trimmedTemplate) {
    warnTemplate(context, 'path template is empty')
    return {
      href: '',
      reason: 'The URL template for this deep link is not configured.',
      missingVariables: [],
    }
  }

  if (!baseUrl && !/^https?:\/\//i.test(trimmedTemplate)) {
    warnTemplate(context, 'base URL is empty; returning disabled URL')
    return {
      href: '',
      reason: 'The Grafana base URL is not configured.',
      missingVariables: [],
    }
  }

  const rendered = fillTemplate(trimmedTemplate, values, context)

  if (rendered.missingVariables.length > 0) {
    return {
      href: '',
      reason: `Missing required deep-link variables: ${rendered.missingVariables.join(', ')}`,
      missingVariables: rendered.missingVariables,
    }
  }

  const safePath = normalizePath(rendered.rendered, context, fallbackPath)
  const href = joinUrl(baseUrl, safePath)

  if (!href) {
    return {
      href: '',
      reason: 'The deep-link template rendered an unusable URL.',
      missingVariables: [],
    }
  }

  return {
    href,
    reason: null,
    missingVariables: [],
  }
}

// Environment configuration is read once at module load and then shared by all UI
// helpers. Values here define API targets and deep-link integration with Argo/Grafana.
export const config = {
  apiBaseUrl: env.VITE_API_BASE_URL ?? '/api',
  argoBaseUrl: env.VITE_ARGO_BASE_URL ?? inferredHomelabDefaults.argoBaseUrl,
  grafanaBaseUrl: env.VITE_GRAFANA_BASE_URL ?? inferredHomelabDefaults.grafanaBaseUrl,
  incidentBannerMinSeverity: env.VITE_INCIDENT_BANNER_MIN_SEVERITY ?? 'warning',
  metricsStaleAfterMinutes: readPositiveNumber(env.VITE_METRICS_STALE_AFTER_MINUTES, 20),
  argoAppPathTemplate: env.VITE_ARGO_APP_PATH_TEMPLATE ?? '/applications/{argoAppName}',
  grafanaDashboardPathTemplate:
    env.VITE_GRAFANA_DASHBOARD_PATH_TEMPLATE ??
    '/d/portal-service-embeds/portal-service-embeds?var-service={serviceId}&var-namespace={namespace}&var-app={appLabel}&var-env={environment}&var-argoApp={argoAppName}&from=now-{timeRange}&to=now',
  grafanaLatencyPanelPathTemplate:
    env.VITE_GRAFANA_LATENCY_PANEL_PATH_TEMPLATE ??
    '/d-solo/portal-service-embeds/portal-service-embeds?panelId=2&var-service={serviceId}&var-namespace={namespace}&var-app={appLabel}&var-env={environment}&var-argoApp={argoAppName}&from=now-{timeRange}&to=now',
  grafanaErrorPanelPathTemplate:
    env.VITE_GRAFANA_ERROR_PANEL_PATH_TEMPLATE ??
    '/d-solo/portal-service-embeds/portal-service-embeds?panelId=3&var-service={serviceId}&var-namespace={namespace}&var-app={appLabel}&var-env={environment}&var-argoApp={argoAppName}&from=now-{timeRange}&to=now',
  lokiLogsPathTemplate:
    env.VITE_LOKI_LOGS_PATH_TEMPLATE ??
    '/explore?var-service={serviceId}&var-namespace={{namespace}}&var-app={{app_label}}&var-env={environment}&var-argoApp={argoAppName}&from=now-{{time_range}}&to=now',
}

interface MonitoringScope {
  serviceId: string
  namespace?: string
  environment?: string
  appLabel?: string
  argoAppName?: string
  timeRange?: string
}

function buildMonitoringValues({
  serviceId,
  namespace,
  environment,
  appLabel,
  argoAppName,
  timeRange,
}: MonitoringScope) {
  return {
    serviceId,
    namespace,
    environment,
    env: environment,
    appLabel,
    app_label: appLabel,
    argoAppName,
    argo_app_name: argoAppName,
    argoApp: argoAppName,
    argo_app: argoAppName,
    timeRange,
    time_range: timeRange,
  }
}

export function buildArgoAppUrl(serviceId: string, argoAppName?: string) {
  return buildMonitoringUrl({
    baseUrl: config.argoBaseUrl,
    pathTemplate: config.argoAppPathTemplate,
    values: {
      serviceId,
      argoAppName: argoAppName ?? serviceId,
    },
    context: 'argo-app-url',
  })
}

export function buildGrafanaDashboardLink(scope: MonitoringScope) {
  return buildMonitoringLink({
    baseUrl: config.grafanaBaseUrl,
    pathTemplate: config.grafanaDashboardPathTemplate,
    values: buildMonitoringValues(scope),
    context: 'grafana-dashboard-url',
  })
}

export function buildGrafanaDashboardUrl(serviceId: string, timeRange = '6h') {
  return buildGrafanaDashboardLink({ serviceId, timeRange }).href
}

export function buildGrafanaLatencyPanelLink(scope: MonitoringScope) {
  return buildMonitoringLink({
    baseUrl: config.grafanaBaseUrl,
    pathTemplate: config.grafanaLatencyPanelPathTemplate,
    values: buildMonitoringValues(scope),
    context: 'grafana-latency-panel-url',
  })
}

export function buildGrafanaLatencyPanelUrl(serviceId: string, timeRange = '6h') {
  return buildGrafanaLatencyPanelLink({ serviceId, timeRange }).href
}

export function buildGrafanaErrorPanelLink(scope: MonitoringScope) {
  return buildMonitoringLink({
    baseUrl: config.grafanaBaseUrl,
    pathTemplate: config.grafanaErrorPanelPathTemplate,
    values: buildMonitoringValues(scope),
    context: 'grafana-error-panel-url',
  })
}

export function buildGrafanaErrorPanelUrl(serviceId: string, timeRange = '6h') {
  return buildGrafanaErrorPanelLink({ serviceId, timeRange }).href
}

interface LogsUrlOptions {
  serviceId: string
  namespace?: string
  environment?: string
  appLabel?: string
  timeRange?: string
  preset?: string
  query?: string
}

export function isLogsConfigured() {
  const probe = buildLogsLink({
    serviceId: 'probe',
    namespace: 'default',
    environment: 'dev',
    appLabel: 'probe',
    argoAppName: 'probe-dev',
    timeRange: '6h',
  })

  return Boolean(probe.href)
}

export function buildLogsLink({
  serviceId,
  namespace,
  environment,
  appLabel,
  argoAppName,
  timeRange,
  preset,
  query,
}: LogsUrlOptions & { argoAppName?: string }) {
  return buildMonitoringLink({
    baseUrl: config.grafanaBaseUrl,
    pathTemplate: config.lokiLogsPathTemplate,
    values: {
      ...buildMonitoringValues({
        serviceId,
        namespace,
        environment,
        appLabel,
        argoAppName,
        timeRange,
      }),
      preset,
      query,
    },
    context: 'loki-logs-url',
  })
}

export function buildLogsUrl(options: LogsUrlOptions) {
  return buildLogsLink(options).href
}
