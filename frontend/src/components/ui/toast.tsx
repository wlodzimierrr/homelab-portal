import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

interface ToastProps {
  message: string
  onClose: () => void
  variant?: 'default' | 'success' | 'error'
}

export function Toast({ message, onClose, variant = 'default' }: ToastProps) {
  return (
    <div
      className={cn(
        'fixed right-4 top-4 z-50 w-full max-w-sm rounded-md border p-3 shadow-lg',
        variant === 'default' && 'border-border bg-card',
        variant === 'success' && 'border-emerald-700/30 bg-emerald-100 text-emerald-950 dark:bg-emerald-950 dark:text-emerald-100',
        variant === 'error' && 'border-destructive/30 bg-destructive/10',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className={cn('text-sm', variant === 'default' && 'text-card-foreground')}>{message}</p>
        <Button type="button" variant="ghost" size="icon" onClick={onClose} aria-label="Close notification">
          <X className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}
