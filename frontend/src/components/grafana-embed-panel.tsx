import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'

type EmbedState = 'loading' | 'ready' | 'failed'

interface GrafanaEmbedPanelProps {
  title: string
  description: string
  embedUrl: string
  dashboardUrl: string
  height?: number
}

export function GrafanaEmbedPanel({
  title,
  description,
  embedUrl,
  dashboardUrl,
  height = 260,
}: GrafanaEmbedPanelProps) {
  const [state, setState] = useState<EmbedState>('loading')
  const [retryToken, setRetryToken] = useState(0)

  const canEmbed = embedUrl.trim().length > 0
  const canOpen = dashboardUrl.trim().length > 0
  const showFallback = state === 'failed' || !canEmbed

  useEffect(() => {
    if (!canEmbed) return

    const timeout = window.setTimeout(() => {
      setState((current) => (current === 'ready' ? current : 'failed'))
    }, 7000)

    return () => window.clearTimeout(timeout)
  }, [canEmbed, embedUrl, retryToken])

  const fallbackMessage = useMemo(() => {
    if (!canEmbed) {
      return 'Grafana embed URL is not configured.'
    }
    return 'Panel failed to load in-app. Use the deep link to open in Grafana.'
  }, [canEmbed])

  return (
    <article className="rounded-md border border-border bg-background p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        {canOpen ? (
          <Button asChild size="sm" variant="outline">
            <a href={dashboardUrl} target="_blank" rel="noreferrer">
              Open in Grafana
            </a>
          </Button>
        ) : (
          <Button type="button" size="sm" variant="outline" disabled>
            Grafana unavailable
          </Button>
        )}
      </div>

      {showFallback ? (
        <div className="rounded-md border border-dashed border-border p-4">
          <p className="text-sm text-muted-foreground">{fallbackMessage}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                setState('loading')
                setRetryToken((value) => value + 1)
              }}
            >
              Retry embed
            </Button>
            {canOpen ? (
              <Button asChild size="sm">
                <a href={dashboardUrl} target="_blank" rel="noreferrer">
                  Open in Grafana
                </a>
              </Button>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          {state === 'loading' ? (
            <div className="flex items-center justify-center bg-muted/40 text-xs text-muted-foreground" style={{ height }}>
              Loading panel...
            </div>
          ) : null}
          <iframe
            key={`${embedUrl}-${retryToken}`}
            src={embedUrl}
            title={title}
            loading="lazy"
            className="w-full"
            style={{ height, display: state === 'loading' ? 'none' : 'block' }}
            onLoad={() => setState('ready')}
            onError={() => setState('failed')}
          />
        </div>
      )}
    </article>
  )
}
