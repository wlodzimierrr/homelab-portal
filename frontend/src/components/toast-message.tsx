import { Toast } from '@/components/ui/toast'

interface ToastMessageProps {
  message: string
  variant?: 'success' | 'error' | 'info'
  onClose?: () => void
}

export function ToastMessage({ message, variant = 'info', onClose }: ToastMessageProps) {
  return <Toast message={message} variant={variant} onClose={onClose} />
}
