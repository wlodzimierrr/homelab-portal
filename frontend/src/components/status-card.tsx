import { cn } from '@/lib/utils'

type HealthState = 'healthy' | 'degraded' | 'unknown'
type SyncState = 'synced' | 'out_of_sync' | 'unknown'

interface StatusCardProps {
  health?: HealthState
  sync?: SyncState
  className?: string
}

interface StatusPillProps {
  label: string
  value: string
}

function getPillTone(value: string) {
  const normalized = value.toLowerCase()

  if (normalized === 'healthy' || normalized === 'synced') {
    return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  }

  if (normalized === 'degraded' || normalized === 'out_of_sync') {
    return 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
  }

  return 'bg-muted text-muted-foreground'
}

function StatusPill({ label, value }: StatusPillProps) {
  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-1 text-xs font-medium', getPillTone(value))}>
      {label}: {value}
    </span>
  )
}

export function StatusCard({ health = 'unknown', sync = 'unknown', className }: StatusCardProps) {
  const isUnavailable = health === 'unknown' && sync === 'unknown'

  return (
    <article className={cn('rounded-md border border-border bg-background p-4', className)}>
      <p className="text-xs uppercase tracking-wide text-muted-foreground">Service Status</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <StatusPill label="Health" value={health} />
        <StatusPill label="Sync" value={sync} />
      </div>
      {isUnavailable ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Live health and sync metadata are not available for this service yet.
        </p>
      ) : null}
    </article>
  )
}
