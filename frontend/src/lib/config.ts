const env = import.meta.env

const TEMPLATE_TOKEN_REGEX = /\{\{\s*([a-zA-Z0-9_]+)\s*\}\}|\{([a-zA-Z0-9_]+)\}/g

interface MonitoringUrlOptions {
  baseUrl: string
  pathTemplate: string
  values: Record<string, string | undefined>
  context: string
  fallbackPath?: string
}

function warnTemplate(context: string, message: string) {
  if (import.meta.env.DEV) {
    console.warn(`[monitoring-url] ${context}: ${message}`)
  }
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

  return rendered
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
  const renderedPath = fillTemplate(pathTemplate, values, context)
  const safePath = normalizePath(renderedPath, context, fallbackPath)

  if (!baseUrl && !/^https?:\/\//i.test(safePath)) {
    warnTemplate(context, 'base URL is empty; returning disabled URL')
    return ''
  }

  return joinUrl(baseUrl, safePath)
}

export const config = {
  apiBaseUrl: env.VITE_API_BASE_URL ?? '/api',
  argoBaseUrl: env.VITE_ARGO_BASE_URL ?? inferredHomelabDefaults.argoBaseUrl,
  grafanaBaseUrl: env.VITE_GRAFANA_BASE_URL ?? inferredHomelabDefaults.grafanaBaseUrl,
  incidentBannerMinSeverity: env.VITE_INCIDENT_BANNER_MIN_SEVERITY ?? 'warning',
  metricsStaleAfterMinutes: readPositiveNumber(env.VITE_METRICS_STALE_AFTER_MINUTES, 20),
  argoAppPathTemplate: env.VITE_ARGO_APP_PATH_TEMPLATE ?? '/applications/{argoAppName}',
  grafanaDashboardPathTemplate:
    env.VITE_GRAFANA_DASHBOARD_PATH_TEMPLATE ?? '/d/service-overview?var-service={serviceId}',
  grafanaLatencyPanelPathTemplate:
    env.VITE_GRAFANA_LATENCY_PANEL_PATH_TEMPLATE ??
    '/d-solo/service-overview/service-overview?panelId=2&var-service={serviceId}&from=now-{timeRange}&to=now',
  grafanaErrorPanelPathTemplate:
    env.VITE_GRAFANA_ERROR_PANEL_PATH_TEMPLATE ??
    '/d-solo/service-overview/service-overview?panelId=3&var-service={serviceId}&from=now-{timeRange}&to=now',
  lokiLogsPathTemplate:
    env.VITE_LOKI_LOGS_PATH_TEMPLATE ??
    '/explore?var-namespace={{namespace}}&var-app={{app_label}}&from=now-{{time_range}}&to=now',
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

export function buildGrafanaDashboardUrl(serviceId: string, timeRange = '6h') {
  return buildMonitoringUrl({
    baseUrl: config.grafanaBaseUrl,
    pathTemplate: config.grafanaDashboardPathTemplate,
    values: {
      serviceId,
      timeRange,
      time_range: timeRange,
    },
    context: 'grafana-dashboard-url',
  })
}

export function buildGrafanaLatencyPanelUrl(serviceId: string, timeRange = '6h') {
  return buildMonitoringUrl({
    baseUrl: config.grafanaBaseUrl,
    pathTemplate: config.grafanaLatencyPanelPathTemplate,
    values: {
      serviceId,
      timeRange,
      time_range: timeRange,
    },
    context: 'grafana-latency-panel-url',
  })
}

export function buildGrafanaErrorPanelUrl(serviceId: string, timeRange = '6h') {
  return buildMonitoringUrl({
    baseUrl: config.grafanaBaseUrl,
    pathTemplate: config.grafanaErrorPanelPathTemplate,
    values: {
      serviceId,
      timeRange,
      time_range: timeRange,
    },
    context: 'grafana-error-panel-url',
  })
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
  const probe = buildLogsUrl({
    serviceId: 'probe',
    namespace: 'default',
    environment: 'dev',
    appLabel: 'probe',
    timeRange: '6h',
  })

  return Boolean(probe)
}

export function buildLogsUrl({ serviceId, namespace, environment, appLabel, timeRange, preset, query }: LogsUrlOptions) {
  return buildMonitoringUrl({
    baseUrl: config.grafanaBaseUrl,
    pathTemplate: config.lokiLogsPathTemplate,
    values: {
      serviceId,
      namespace,
      environment,
      app_label: appLabel,
      time_range: timeRange,
      appLabel,
      timeRange,
      preset,
      query,
    },
    context: 'loki-logs-url',
  })
}
