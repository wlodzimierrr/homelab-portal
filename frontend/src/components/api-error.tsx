import { Button } from '@/components/ui/button'

// Compact shared error block for API-backed widgets that can optionally retry.
interface ApiErrorProps {
  message: string
  onRetry?: () => void
}

export function ApiError({ message, onRetry }: ApiErrorProps) {
  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3">
      <p className="text-sm text-destructive">{message}</p>
      {onRetry ? (
        <Button type="button" variant="outline" size="sm" className="mt-3" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  )
}
