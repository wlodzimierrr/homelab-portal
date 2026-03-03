interface LoadingStateProps {
  label?: string
  rows?: number
}

export function LoadingState({ label = 'Loading data...', rows = 3 }: LoadingStateProps) {
  return (
    <div className="space-y-3" aria-live="polite" aria-busy="true">
      <p className="text-sm text-muted-foreground">{label}</p>
      <div className="space-y-2">
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className="h-12 animate-pulse rounded-md border border-border bg-muted/50" />
        ))}
      </div>
    </div>
  )
}
