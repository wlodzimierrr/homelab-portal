import { EmptyState } from '@/components/empty-state'

export function Empty(props: Parameters<typeof EmptyState>[0]) {
  return <EmptyState {...props} />
}
