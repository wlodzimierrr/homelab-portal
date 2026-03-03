import type { AnchorHTMLAttributes, MouseEvent } from 'react'

interface AppLinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  to: string
}

export function AppLink({ to, onClick, ...props }: AppLinkProps) {
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event)
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return
    }
    if (to.startsWith('http')) {
      return
    }

    event.preventDefault()
    window.history.pushState({}, '', to)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }

  return <a href={to} onClick={handleClick} {...props} />
}
