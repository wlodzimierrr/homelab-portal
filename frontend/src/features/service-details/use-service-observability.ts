import { useCallback, useEffect, useState } from 'react'
import {
  createEmptyServiceMetricsSummary,
  createEmptyServiceMetricsTrends,
  getServiceMetricsSummary,
  getServiceMetricsTrends,
  type ServiceMetricsRange,
} from '@/lib/adapters/service-metrics'
import {
  getServiceLogsQuickView,
  type LogsQuickViewPreset,
  type LogsQuickViewRange,
  type ServiceLogsQuickView,
} from '@/lib/adapters/logs-quickview'
import {
  getServiceHealthTimeline,
  type ServiceHealthTimeline as ServiceHealthTimelineData,
  type TimelineWindow,
} from '@/lib/adapters/service-health-timeline'
import type { ServiceIdentity } from '@/lib/service-identity'

export function useServiceObservability(serviceIdentity: ServiceIdentity) {
  const [metricsRange, setMetricsRange] = useState<ServiceMetricsRange>('24h')
  const [metrics, setMetrics] = useState(() =>
    createEmptyServiceMetricsSummary(serviceIdentity, '24h'),
  )
  const [metricsLoading, setMetricsLoading] = useState(true)
  const [metricsError, setMetricsError] = useState('')
  const [metricTrends, setMetricTrends] = useState(() =>
    createEmptyServiceMetricsTrends(serviceIdentity, '24h'),
  )
  const [metricTrendsLoading, setMetricTrendsLoading] = useState(true)
  const [metricTrendsError, setMetricTrendsError] = useState('')
  const [timelineWindow, setTimelineWindow] = useState<TimelineWindow>('24h')
  const [timeline, setTimeline] = useState<ServiceHealthTimelineData | null>(null)
  const [timelineLoading, setTimelineLoading] = useState(true)
  const [timelineError, setTimelineError] = useState('')
  const [activeLogsPreset, setActiveLogsPreset] = useState<LogsQuickViewPreset>('all')
  const [logsRange, setLogsRange] = useState<LogsQuickViewRange>('1h')
  const [logsResult, setLogsResult] = useState<ServiceLogsQuickView | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)
  const [logsError, setLogsError] = useState('')

  const loadMetrics = useCallback(async () => {
    setMetricsLoading(true)
    setMetricsError('')

    try {
      const response = await getServiceMetricsSummary(serviceIdentity, metricsRange)
      setMetrics(response)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load service metrics'
      setMetricsError(message)
      setMetrics(createEmptyServiceMetricsSummary(serviceIdentity, metricsRange))
    } finally {
      setMetricsLoading(false)
    }
  }, [metricsRange, serviceIdentity])

  useEffect(() => {
    void loadMetrics()
  }, [loadMetrics])

  const loadMetricTrends = useCallback(async () => {
    setMetricTrendsLoading(true)
    setMetricTrendsError('')

    try {
      const response = await getServiceMetricsTrends(serviceIdentity, metricsRange)
      setMetricTrends(response)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load service metric trends'
      setMetricTrendsError(message)
      setMetricTrends(createEmptyServiceMetricsTrends(serviceIdentity, metricsRange))
    } finally {
      setMetricTrendsLoading(false)
    }
  }, [metricsRange, serviceIdentity])

  useEffect(() => {
    void loadMetricTrends()
  }, [loadMetricTrends])

  const loadTimeline = useCallback(async () => {
    setTimelineLoading(true)
    setTimelineError('')

    try {
      const response = await getServiceHealthTimeline(serviceIdentity, timelineWindow)
      setTimeline(response)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load service timeline'
      setTimelineError(message)
    } finally {
      setTimelineLoading(false)
    }
  }, [serviceIdentity, timelineWindow])

  useEffect(() => {
    void loadTimeline()
  }, [loadTimeline])

  const loadQuickViewLogs = useCallback(async () => {
    setLogsLoading(true)
    setLogsError('')

    try {
      const response = await getServiceLogsQuickView(serviceIdentity, {
        preset: activeLogsPreset,
        range: logsRange,
      })
      setLogsResult(response)
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'Failed to load logs quick view'
      setLogsError(message)
      setLogsResult(null)
    } finally {
      setLogsLoading(false)
    }
  }, [activeLogsPreset, logsRange, serviceIdentity])

  useEffect(() => {
    void loadQuickViewLogs()
  }, [loadQuickViewLogs])

  return {
    metricsRange,
    setMetricsRange,
    metrics,
    metricsLoading,
    metricsError,
    metricTrends,
    metricTrendsLoading,
    metricTrendsError,
    timelineWindow,
    setTimelineWindow,
    timeline,
    timelineLoading,
    timelineError,
    activeLogsPreset,
    setActiveLogsPreset,
    logsRange,
    setLogsRange,
    logsResult,
    logsLoading,
    logsError,
    loadMetrics,
    loadMetricTrends,
    loadTimeline,
    loadQuickViewLogs,
  }
}
