import { Toast } from '@/components/ui/toast'

interface ToastMessageProps {
  message: string
  variant?: 'success' | 'error' | 'info'
  onClose?: () => void
}

export function ToastMessage({ message, variant = 'info', onClose }: ToastMessageProps) {
  const mappedVariant = variant === 'info' ? 'default' : variant
  return <Toast message={message} variant={mappedVariant} onClose={onClose ?? (() => {})} />
}
