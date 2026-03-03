const env = import.meta.env

export const config = {
  apiBaseUrl: env.VITE_API_BASE_URL ?? '/api',
  argoBaseUrl: env.VITE_ARGO_BASE_URL ?? 'http://argo.dev.homelab.local',
  grafanaBaseUrl: env.VITE_GRAFANA_BASE_URL ?? 'http://grafana.dev.homelab.local',
}
