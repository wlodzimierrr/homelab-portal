export const UNAUTHORIZED_EVENT = 'portal:unauthorized'

export interface UnauthorizedEventDetail {
  message: string
}

export function dispatchUnauthorized(message = 'Your session expired. Please sign in again.') {
  window.dispatchEvent(
    new CustomEvent<UnauthorizedEventDetail>(UNAUTHORIZED_EVENT, {
      detail: { message },
    }),
  )
}
