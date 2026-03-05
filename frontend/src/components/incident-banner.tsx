import { Button } from '@/components/ui/button'
import type { IncidentSeverity } from '@/lib/incident-alerts'
import { cn } from '@/lib/utils'

interface IncidentBannerProps {
  activeCount: number
  highestSeverity: IncidentSeverity
  onDismiss: () => void
}

function getTone(severity: IncidentSeverity) {
  if (severity === 'critical') {
    return 'border-rose-500/60 bg-rose-500/10 text-rose-800 dark:text-rose-200'
  }
  if (severity === 'warning') {
    return 'border-amber-500/60 bg-amber-500/10 text-amber-800 dark:text-amber-200'
  }
  return 'border-sky-500/60 bg-sky-500/10 text-sky-800 dark:text-sky-200'
}

function formatSeverityLabel(severity: IncidentSeverity) {
  return severity === 'critical' ? 'Critical' : severity === 'warning' ? 'Warning' : 'Info'
}

export function IncidentBanner({ activeCount, highestSeverity, onDismiss }: IncidentBannerProps) {
  return (
    <div className={cn('mx-4 mt-4 rounded-md border px-4 py-3 md:mx-6', getTone(highestSeverity))}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold">
            {formatSeverityLabel(highestSeverity)} monitoring incidents active ({activeCount})
          </p>
          <p className="text-xs opacity-90">Monitoring signals indicate active platform issues requiring attention.</p>
        </div>
        {highestSeverity !== 'critical' ? (
          <Button type="button" size="sm" variant="outline" onClick={onDismiss}>
            Dismiss for session
          </Button>
        ) : null}
      </div>
    </div>
  )
}
