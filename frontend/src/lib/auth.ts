import { useCallback, useEffect, useState } from 'react'
import { getAuthMode } from '@/lib/config'

// Auth state is stored in localStorage so refreshes survive, and mirrored through
// a custom event because the browser `storage` event does not fire in the same tab.
const AUTH_TOKEN_KEY = 'portal-auth-token'
const AUTH_TOKEN_CHANGED_EVENT = 'portal:auth-token-changed'

function emitTokenChanged() {
  window.dispatchEvent(new Event(AUTH_TOKEN_CHANGED_EVENT))
}

export function getToken() {
  if (getAuthMode() !== 'bearer_token') {
    return null
  }
  return window.localStorage.getItem(AUTH_TOKEN_KEY)
}

export function setToken(token: string) {
  window.localStorage.setItem(AUTH_TOKEN_KEY, token)
  emitTokenChanged()
}

export function clearToken() {
  window.localStorage.removeItem(AUTH_TOKEN_KEY)
  emitTokenChanged()
}

export function useAuth() {
  const authMode = getAuthMode()
  const [token, setTokenState] = useState<string | null>(() => getToken())

  useEffect(() => {
    // Listen to both cross-tab changes and same-tab updates triggered by helpers above.
    const sync = () => setTokenState(getToken())
    const onStorage = (event: StorageEvent) => {
      if (event.key === AUTH_TOKEN_KEY) {
        sync()
      }
    }

    window.addEventListener('storage', onStorage)
    window.addEventListener(AUTH_TOKEN_CHANGED_EVENT, sync)

    return () => {
      window.removeEventListener('storage', onStorage)
      window.removeEventListener(AUTH_TOKEN_CHANGED_EVENT, sync)
    }
  }, [])

  const saveToken = useCallback((nextToken: string) => {
    setToken(nextToken)
  }, [])

  const removeToken = useCallback(() => {
    clearToken()
  }, [])

  return {
    authMode,
    token,
    isAuthenticated: authMode === 'forwarded_identity' ? true : Boolean(token),
    setToken: saveToken,
    clearToken: removeToken,
  }
}
