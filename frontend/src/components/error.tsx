import { ErrorState } from '@/components/error-state'

export function Error(props: Parameters<typeof ErrorState>[0]) {
  return <ErrorState {...props} />
}
