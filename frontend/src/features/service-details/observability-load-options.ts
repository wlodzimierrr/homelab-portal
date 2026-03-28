export interface ServiceObservabilityLoadOptions {
  loadMetrics?: boolean
  loadTrends?: boolean
  loadTimeline?: boolean
  loadLogs?: boolean
}

export interface ResolvedServiceObservabilityLoadOptions {
  loadMetrics: boolean
  loadTrends: boolean
  loadTimeline: boolean
  loadLogs: boolean
}

export function resolveServiceObservabilityLoadOptions(
  options?: ServiceObservabilityLoadOptions,
): ResolvedServiceObservabilityLoadOptions {
  return {
    loadMetrics: options?.loadMetrics ?? true,
    loadTrends: options?.loadTrends ?? true,
    loadTimeline: options?.loadTimeline ?? true,
    loadLogs: options?.loadLogs ?? true,
  }
}
