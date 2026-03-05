import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { type ServiceHealthTimelineSegment, type TimelineStatus } from '@/lib/adapters/service-health-timeline'

interface ServiceHealthTimelineProps {
  segments: ServiceHealthTimelineSegment[]
  lastRefreshedAt?: string
  isLoading?: boolean
}

function tone(status: TimelineStatus) {
  if (status === 'healthy') {
    return 'bg-emerald-500/80'
  }
  if (status === 'degraded') {
    return 'bg-amber-500/80'
  }
  if (status === 'down') {
    return 'bg-rose-500/80'
  }
  return 'bg-muted-foreground/40'
}

function formatDate(value?: string) {
  if (!value) return 'N/A'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'N/A'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)
}

function formatDuration(startAt: string, endAt: string) {
  const start = new Date(startAt).getTime()
  const end = new Date(endAt).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return 'N/A'
  }

  const totalMinutes = Math.round((end - start) / 60000)
  if (totalMinutes < 60) {
    return `${totalMinutes} min`
  }

  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (minutes === 0) {
    return `${hours} h`
  }
  return `${hours} h ${minutes} min`
}

export function ServiceHealthTimeline({ segments, lastRefreshedAt, isLoading }: ServiceHealthTimelineProps) {
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(null)
  const [hoveredSegmentId, setHoveredSegmentId] = useState<string | null>(null)

  const totalDuration = useMemo(() => {
    if (segments.length === 0) return 0
    return segments.reduce((sum, segment) => {
      const start = new Date(segment.startAt).getTime()
      const end = new Date(segment.endAt).getTime()
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
        return sum
      }
      return sum + (end - start)
    }, 0)
  }, [segments])

  const selectedStillExists = selectedSegmentId
    ? segments.some((segment) => segment.id === selectedSegmentId)
    : false
  const activeId = hoveredSegmentId ?? (selectedStillExists ? selectedSegmentId : null) ?? segments[0]?.id ?? null
  const activeSegment = segments.find((segment) => segment.id === activeId) ?? null

  if (isLoading) {
    return (
      <div className="rounded-md border border-border bg-background p-4">
        <div className="h-5 w-40 animate-pulse rounded bg-muted" />
        <div className="mt-3 flex h-8 gap-2">
          <div className="h-full flex-1 animate-pulse rounded bg-muted" />
          <div className="h-full flex-1 animate-pulse rounded bg-muted" />
          <div className="h-full flex-1 animate-pulse rounded bg-muted" />
        </div>
      </div>
    )
  }

  if (segments.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border bg-background p-4 text-sm text-muted-foreground">
        No health timeline data available for this window.
      </div>
    )
  }

  return (
    <div className="space-y-3 rounded-md border border-border bg-background p-4">
      <div className="overflow-x-auto">
        <div className="flex min-w-[320px] gap-1 md:min-w-[520px]">
          {segments.map((segment) => {
            const start = new Date(segment.startAt).getTime()
            const end = new Date(segment.endAt).getTime()
            const duration = Number.isFinite(start) && Number.isFinite(end) && end > start ? end - start : 0
            const basisPct = totalDuration > 0 ? Math.max(6, (duration / totalDuration) * 100) : 10
            const isActive = segment.id === activeId

            return (
              <button
                key={segment.id}
                type="button"
                onClick={() => setSelectedSegmentId(segment.id)}
                onMouseEnter={() => setHoveredSegmentId(segment.id)}
                onMouseLeave={() => setHoveredSegmentId(null)}
                onFocus={() => setHoveredSegmentId(segment.id)}
                onBlur={() => setHoveredSegmentId(null)}
                className={cn(
                  'h-8 rounded transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary',
                  tone(segment.status),
                  isActive ? 'ring-2 ring-primary/70 ring-offset-1 ring-offset-background' : 'opacity-85 hover:opacity-100',
                )}
                style={{ flexBasis: `${basisPct}%` }}
                title={`${segment.status} | ${formatDate(segment.startAt)} - ${formatDate(segment.endAt)}`}
                aria-label={`${segment.status} segment from ${formatDate(segment.startAt)} to ${formatDate(segment.endAt)}`}
              />
            )
          })}
        </div>
      </div>

      {activeSegment ? (
        <div className="grid gap-2 rounded-md border border-border/70 bg-muted/20 p-3 text-xs md:grid-cols-2">
          <p>
            <span className="text-muted-foreground">Status:</span>{' '}
            <span className="font-medium capitalize">{activeSegment.status}</span>
          </p>
          <p>
            <span className="text-muted-foreground">Duration:</span> {formatDuration(activeSegment.startAt, activeSegment.endAt)}
          </p>
          <p>
            <span className="text-muted-foreground">From:</span> {formatDate(activeSegment.startAt)}
          </p>
          <p>
            <span className="text-muted-foreground">To:</span> {formatDate(activeSegment.endAt)}
          </p>
          <p className="md:col-span-2">
            <span className="text-muted-foreground">Reason:</span> {activeSegment.reason?.trim() ? activeSegment.reason : 'N/A'}
          </p>
        </div>
      ) : null}

      <p className="text-xs text-muted-foreground">Last refresh: {formatDate(lastRefreshedAt)}</p>
    </div>
  )
}
