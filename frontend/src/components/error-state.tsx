import { ApiError } from '@/components/api-error'

interface ErrorStateProps {
  message: string
  onRetry?: () => void
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return <ApiError message={message} onRetry={onRetry} />
}
