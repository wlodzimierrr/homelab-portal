import { ToastMessage } from '@/components/toast-message'

export function Toast(props: Parameters<typeof ToastMessage>[0]) {
  return <ToastMessage {...props} />
}
