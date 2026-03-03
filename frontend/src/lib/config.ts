const env = import.meta.env

function fillTemplate(template: string, values: Record<string, string | undefined>) {
  return template.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}|\{([a-zA-Z0-9_]+)\}/g, (_match, mustacheKey, braceKey) => {
    const key = (mustacheKey ?? braceKey) as string
    const value = values[key]
    return value ? encodeURIComponent(value) : ''
  })
}

function joinUrl(baseUrl: string, path: string) {
  if (!baseUrl) {
    return ''
  }
  const normalizedBase = baseUrl.replace(/\/+$/, '')
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${normalizedBase}${normalizedPath}`
}

export const config = {
  apiBaseUrl: env.VITE_API_BASE_URL ?? '/api',
  argoBaseUrl: env.VITE_ARGO_BASE_URL ?? '',
  grafanaBaseUrl: env.VITE_GRAFANA_BASE_URL ?? '',
  argoAppPathTemplate: env.VITE_ARGO_APP_PATH_TEMPLATE ?? '/applications/{serviceId}',
  grafanaDashboardPathTemplate:
    env.VITE_GRAFANA_DASHBOARD_PATH_TEMPLATE ?? '/d/service-overview?var-service={serviceId}',
  lokiLogsPathTemplate:
    env.VITE_LOKI_LOGS_PATH_TEMPLATE ??
    '/explore?var-namespace={{namespace}}&var-app={{app_label}}&from=now-{{time_range}}&to=now',
}

export function buildArgoAppUrl(serviceId: string) {
  return joinUrl(config.argoBaseUrl, fillTemplate(config.argoAppPathTemplate, { serviceId }))
}

export function buildGrafanaDashboardUrl(serviceId: string) {
  return joinUrl(
    config.grafanaBaseUrl,
    fillTemplate(config.grafanaDashboardPathTemplate, { serviceId }),
  )
}

interface LogsUrlOptions {
  serviceId: string
  namespace?: string
  appLabel?: string
  timeRange?: string
}

export function isLogsConfigured() {
  return Boolean(config.grafanaBaseUrl.trim() && config.lokiLogsPathTemplate.trim())
}

export function buildLogsUrl({ serviceId, namespace, appLabel, timeRange }: LogsUrlOptions) {
  return joinUrl(
    config.grafanaBaseUrl,
    fillTemplate(config.lokiLogsPathTemplate, {
      serviceId,
      namespace,
      app_label: appLabel,
      time_range: timeRange,
      appLabel,
      timeRange,
    }),
  )
}
