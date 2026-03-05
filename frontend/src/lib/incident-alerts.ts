export type IncidentSeverity = 'info' | 'warning' | 'critical'

interface IncidentInput {
  severity: IncidentSeverity
  status: 'active' | 'resolved'
  serviceId?: string
}

export interface ServiceIncidentBadge {
  total: number
  critical: number
  warning: number
  info: number
  highestSeverity: IncidentSeverity | null
}

export interface IncidentAlertSnapshot {
  activeCount: number
  highestSeverity: IncidentSeverity | null
  serviceAlerts: Record<string, ServiceIncidentBadge>
}

const severityPriority: Record<IncidentSeverity, number> = {
  info: 1,
  warning: 2,
  critical: 3,
}

export function normalizeIncidentSeverityThreshold(value: string | undefined): IncidentSeverity {
  if (!value) {
    return 'warning'
  }

  const normalized = value.trim().toLowerCase()
  if (normalized === 'critical') {
    return 'critical'
  }
  if (normalized === 'info') {
    return 'info'
  }
  return 'warning'
}

function maxSeverity(left: IncidentSeverity | null, right: IncidentSeverity): IncidentSeverity {
  if (!left) {
    return right
  }
  return severityPriority[right] > severityPriority[left] ? right : left
}

function emptyServiceBadge(): ServiceIncidentBadge {
  return {
    total: 0,
    critical: 0,
    warning: 0,
    info: 0,
    highestSeverity: null,
  }
}

export function buildIncidentAlertSnapshot(incidents: IncidentInput[]): IncidentAlertSnapshot {
  const serviceAlerts: Record<string, ServiceIncidentBadge> = {}
  let activeCount = 0
  let highestSeverity: IncidentSeverity | null = null

  for (const incident of incidents) {
    if (incident.status !== 'active') {
      continue
    }

    activeCount += 1
    highestSeverity = maxSeverity(highestSeverity, incident.severity)

    if (!incident.serviceId) {
      continue
    }

    const current = serviceAlerts[incident.serviceId] ?? emptyServiceBadge()
    current.total += 1
    current[incident.severity] += 1
    current.highestSeverity = maxSeverity(current.highestSeverity, incident.severity)
    serviceAlerts[incident.serviceId] = current
  }

  return {
    activeCount,
    highestSeverity,
    serviceAlerts,
  }
}

export function shouldShowIncidentBanner(
  snapshot: IncidentAlertSnapshot,
  options: {
    threshold: IncidentSeverity
    dismissed: boolean
  },
) {
  const { threshold, dismissed } = options
  if (!snapshot.highestSeverity) {
    return false
  }

  if (severityPriority[snapshot.highestSeverity] < severityPriority[threshold]) {
    return false
  }

  if (snapshot.highestSeverity === 'critical') {
    return true
  }

  return !dismissed
}
