import { LoadingState } from '@/components/loading-state'

export function Loading(props: Parameters<typeof LoadingState>[0]) {
  return <LoadingState {...props} />
}
