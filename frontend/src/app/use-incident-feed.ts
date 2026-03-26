import { useCallback, useEffect, useMemo, useState } from 'react'
import { getPlatformIncidentFeed } from '@/lib/adapters/platform-health'
import { config } from '@/lib/config'
import {
  buildIncidentAlertSnapshot,
  normalizeIncidentSeverityThreshold,
  shouldShowIncidentBanner,
  type IncidentAlertSnapshot,
} from '@/lib/incident-alerts'

const INCIDENT_BANNER_DISMISSED_KEY = 'portal-incident-banner-dismissed'
const INCIDENT_POLL_INTERVAL_MS = 60_000

const EMPTY_INCIDENT_SNAPSHOT: IncidentAlertSnapshot = {
  activeCount: 0,
  highestSeverity: null,
  serviceAlerts: {},
}

export function useIncidentFeed(token: string | null, pathname: string) {
  const [incidentSnapshot, setIncidentSnapshot] = useState<IncidentAlertSnapshot>(EMPTY_INCIDENT_SNAPSHOT)
  const [isIncidentBannerDismissed, setIsIncidentBannerDismissed] = useState(() => {
    return window.sessionStorage.getItem(INCIDENT_BANNER_DISMISSED_KEY) === '1'
  })

  // Incident polling stays at the shell level so layout chrome and individual
  // pages can reuse one snapshot instead of issuing duplicate observability reads.
  useEffect(() => {
    if (!token) {
      setIncidentSnapshot(EMPTY_INCIDENT_SNAPSHOT)
      return
    }

    let cancelled = false

    const loadIncidents = async () => {
      try {
        const feed = await getPlatformIncidentFeed()
        if (cancelled) {
          return
        }
        setIncidentSnapshot(buildIncidentAlertSnapshot(feed.incidents))
      } catch {
        if (cancelled) {
          return
        }
        setIncidentSnapshot(EMPTY_INCIDENT_SNAPSHOT)
      }
    }

    void loadIncidents()
    const intervalId = window.setInterval(() => void loadIncidents(), INCIDENT_POLL_INTERVAL_MS)

    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [token])

  const dismissIncidentBanner = useCallback(() => {
    setIsIncidentBannerDismissed(true)
    window.sessionStorage.setItem(INCIDENT_BANNER_DISMISSED_KEY, '1')
  }, [])

  const incidentThreshold = useMemo(
    () => normalizeIncidentSeverityThreshold(config.incidentBannerMinSeverity),
    [],
  )

  const showIncidentBanner = useMemo(() => {
    return (
      pathname !== '/login' &&
      shouldShowIncidentBanner(incidentSnapshot, {
        threshold: incidentThreshold,
        dismissed: isIncidentBannerDismissed,
      })
    )
  }, [incidentSnapshot, incidentThreshold, isIncidentBannerDismissed, pathname])

  return {
    incidentSnapshot,
    showIncidentBanner,
    dismissIncidentBanner,
  }
}
