export type DeploymentAlertLevel = 'none' | 'warning' | 'critical'

export interface DeploymentAlertThresholds {
  warningRegressionScore: number
  criticalRegressionScore: number
  warningErrorRateDeltaPct: number
  criticalErrorRateDeltaPct: number
  warningLatencyDeltaMs: number
  criticalLatencyDeltaMs: number
  warningAvailabilityDropPct: number
  criticalAvailabilityDropPct: number
  suspiciousOutcomes: string[]
}

export interface DeploymentAlertResult {
  level: DeploymentAlertLevel
  suspicious: boolean
  reasons: string[]
  priority: number
}

interface DeploymentSignal {
  outcome?: string
  regressionScore?: number
  errorRateDeltaPct?: number
  latencyDeltaMs?: number
  availabilityDeltaPct?: number
}

interface DeploymentHistorySignal {
  outcome: string
  regressionScore: number
  errorRatePct: { delta?: number }
  p95LatencyMs: { delta?: number }
  availabilityPct: { delta?: number }
}

export const DEFAULT_DEPLOYMENT_ALERT_THRESHOLDS: DeploymentAlertThresholds = {
  warningRegressionScore: 0.8,
  criticalRegressionScore: 1.5,
  warningErrorRateDeltaPct: 0.4,
  criticalErrorRateDeltaPct: 1.0,
  warningLatencyDeltaMs: 80,
  criticalLatencyDeltaMs: 180,
  warningAvailabilityDropPct: 0.12,
  criticalAvailabilityDropPct: 0.3,
  suspiciousOutcomes: ['failed', 'degraded', 'error'],
}

function normalizeOutcome(value?: string) {
  return value?.trim().toLowerCase() ?? ''
}

function evaluateThreshold(
  value: number | undefined,
  warning: number,
  critical: number,
  reasonLabel: string,
): { level: DeploymentAlertLevel; reason?: string; priority: number } {
  if (typeof value !== 'number') {
    return { level: 'none', priority: 0 }
  }

  if (value >= critical) {
    return {
      level: 'critical',
      reason: `${reasonLabel} critical (${value.toFixed(2)})`,
      priority: 3,
    }
  }

  if (value >= warning) {
    return {
      level: 'warning',
      reason: `${reasonLabel} warning (${value.toFixed(2)})`,
      priority: 2,
    }
  }

  return { level: 'none', priority: 0 }
}

export function evaluateDeploymentAlert(
  signal: DeploymentSignal,
  thresholds: DeploymentAlertThresholds = DEFAULT_DEPLOYMENT_ALERT_THRESHOLDS,
): DeploymentAlertResult {
  let level: DeploymentAlertLevel = 'none'
  let priority = 0
  const reasons: string[] = []

  const normalizedOutcome = normalizeOutcome(signal.outcome)
  if (thresholds.suspiciousOutcomes.includes(normalizedOutcome)) {
    level = 'critical'
    priority = Math.max(priority, 4)
    reasons.push(`outcome=${normalizedOutcome || 'unknown'}`)
  }

  const scoreResult = evaluateThreshold(
    signal.regressionScore,
    thresholds.warningRegressionScore,
    thresholds.criticalRegressionScore,
    'regression score',
  )
  if (scoreResult.reason) {
    reasons.push(scoreResult.reason)
    if (scoreResult.level === 'critical') level = 'critical'
    if (scoreResult.level === 'warning' && level === 'none') level = 'warning'
    priority = Math.max(priority, scoreResult.priority)
  }

  const errorResult = evaluateThreshold(
    signal.errorRateDeltaPct,
    thresholds.warningErrorRateDeltaPct,
    thresholds.criticalErrorRateDeltaPct,
    'error delta',
  )
  if (errorResult.reason) {
    reasons.push(errorResult.reason)
    if (errorResult.level === 'critical') level = 'critical'
    if (errorResult.level === 'warning' && level === 'none') level = 'warning'
    priority = Math.max(priority, errorResult.priority)
  }

  const latencyResult = evaluateThreshold(
    signal.latencyDeltaMs,
    thresholds.warningLatencyDeltaMs,
    thresholds.criticalLatencyDeltaMs,
    'latency delta',
  )
  if (latencyResult.reason) {
    reasons.push(latencyResult.reason)
    if (latencyResult.level === 'critical') level = 'critical'
    if (latencyResult.level === 'warning' && level === 'none') level = 'warning'
    priority = Math.max(priority, latencyResult.priority)
  }

  const availabilityDrop = typeof signal.availabilityDeltaPct === 'number' ? -signal.availabilityDeltaPct : undefined
  const availabilityResult = evaluateThreshold(
    availabilityDrop,
    thresholds.warningAvailabilityDropPct,
    thresholds.criticalAvailabilityDropPct,
    'availability drop',
  )
  if (availabilityResult.reason) {
    reasons.push(availabilityResult.reason)
    if (availabilityResult.level === 'critical') level = 'critical'
    if (availabilityResult.level === 'warning' && level === 'none') level = 'warning'
    priority = Math.max(priority, availabilityResult.priority)
  }

  return {
    level,
    suspicious: level !== 'none',
    reasons,
    priority,
  }
}

export function evaluateDeploymentHistoryItem(
  item: DeploymentHistorySignal,
  thresholds: DeploymentAlertThresholds = DEFAULT_DEPLOYMENT_ALERT_THRESHOLDS,
) {
  return evaluateDeploymentAlert(
    {
      outcome: item.outcome,
      regressionScore: item.regressionScore,
      errorRateDeltaPct: item.errorRatePct.delta,
      latencyDeltaMs: item.p95LatencyMs.delta,
      availabilityDeltaPct: item.availabilityPct.delta,
    },
    thresholds,
  )
}

export function summarizeDeploymentAlerts(
  items: Array<{
    outcome?: string
    regressionScore?: number
    errorRateDeltaPct?: number
    latencyDeltaMs?: number
    availabilityDeltaPct?: number
  }>,
  thresholds: DeploymentAlertThresholds = DEFAULT_DEPLOYMENT_ALERT_THRESHOLDS,
): DeploymentAlertResult {
  let level: DeploymentAlertLevel = 'none'
  let priority = 0
  const reasons = new Set<string>()

  for (const item of items) {
    const result = evaluateDeploymentAlert(item, thresholds)
    if (result.level === 'critical') {
      level = 'critical'
    } else if (result.level === 'warning' && level === 'none') {
      level = 'warning'
    }
    priority = Math.max(priority, result.priority)
    for (const reason of result.reasons) {
      reasons.add(reason)
    }
  }

  return {
    level,
    suspicious: level !== 'none',
    reasons: [...reasons],
    priority,
  }
}
