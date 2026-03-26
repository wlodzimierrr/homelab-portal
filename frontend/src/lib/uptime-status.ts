// Uptime classification is intentionally small and presentation-oriented so UI
// components can color-code metrics without reimplementing thresholds.
export type UptimeSeverity = 'healthy' | 'warning' | 'critical' | 'unknown'

export interface UptimeThresholdConfig {
  healthyMin: number
  warningMin: number
  staleAfterMinutes: number
}

export const DEFAULT_UPTIME_THRESHOLD_CONFIG: UptimeThresholdConfig = {
  healthyMin: 99.9,
  warningMin: 99.0,
  staleAfterMinutes: 20,
}

export function classifyUptime(
  value: number | undefined,
  config: UptimeThresholdConfig = DEFAULT_UPTIME_THRESHOLD_CONFIG,
): UptimeSeverity {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 'unknown'
  }

  if (value >= config.healthyMin) {
    return 'healthy'
  }

  if (value >= config.warningMin) {
    return 'warning'
  }

  return 'critical'
}

// Staleness is tracked separately from severity because a stale healthy value
// should not be shown as trustworthy fresh telemetry.
export function isMetricStale(
  lastRefreshedAt: string | undefined,
  config: UptimeThresholdConfig = DEFAULT_UPTIME_THRESHOLD_CONFIG,
): boolean {
  if (!lastRefreshedAt) {
    return false
  }

  const parsed = new Date(lastRefreshedAt)
  if (Number.isNaN(parsed.getTime())) {
    return false
  }

  const ageMs = Date.now() - parsed.getTime()
  return ageMs > config.staleAfterMinutes * 60 * 1000
}

// Rollups prefer the worst available state and fall back to unknown when no
// signal is present. This keeps cards and aggregates aligned.
export function mergeUptimeSeverities(values: UptimeSeverity[]): UptimeSeverity {
  if (values.includes('critical')) {
    return 'critical'
  }
  if (values.includes('warning')) {
    return 'warning'
  }
  if (values.includes('healthy')) {
    return 'healthy'
  }
  return 'unknown'
}
