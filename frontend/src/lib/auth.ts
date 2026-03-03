import { useCallback, useEffect, useState } from 'react'

const AUTH_TOKEN_KEY = 'portal-auth-token'
const AUTH_TOKEN_CHANGED_EVENT = 'portal:auth-token-changed'

function emitTokenChanged() {
  window.dispatchEvent(new Event(AUTH_TOKEN_CHANGED_EVENT))
}

export function getToken() {
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
  const [token, setTokenState] = useState<string | null>(() => getToken())

  useEffect(() => {
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
    token,
    isAuthenticated: Boolean(token),
    setToken: saveToken,
    clearToken: removeToken,
  }
}
