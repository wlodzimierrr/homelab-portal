import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

interface MockBrowserOptions {
  pathname?: string
  hostname?: string
  token?: string
}

function createStorage() {
  const store = new Map<string, string>()

  return {
    getItem(key: string) {
      return store.has(key) ? store.get(key) ?? null : null
    },
    setItem(key: string, value: string) {
      store.set(key, String(value))
    },
    removeItem(key: string) {
      store.delete(key)
    },
    clear() {
      store.clear()
    },
  }
}

export function installMockBrowser({
  pathname = '/services/demo-service',
  hostname = 'portal.test',
  token = 'test-token',
}: MockBrowserOptions = {}) {
  const previousWindow = globalThis.window
  const previousDocument = globalThis.document

  const localStorage = createStorage()
  const sessionStorage = createStorage()

  if (token) {
    localStorage.setItem('portal-auth-token', token)
  }

  localStorage.setItem('portal-theme', 'light')

  const listeners = new Map<string, Set<EventListenerOrEventListenerObject>>()

  const location = {
    pathname,
    hostname,
  }

  const windowStub = {
    location,
    history: {
      pushState(_state: unknown, _title: string, nextPath: string) {
        location.pathname = nextPath
      },
      replaceState(_state: unknown, _title: string, nextPath: string) {
        location.pathname = nextPath
      },
    },
    localStorage,
    sessionStorage,
    addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
      const entry = listeners.get(type) ?? new Set<EventListenerOrEventListenerObject>()
      entry.add(listener)
      listeners.set(type, entry)
    },
    removeEventListener(type: string, listener: EventListenerOrEventListenerObject) {
      listeners.get(type)?.delete(listener)
    },
    dispatchEvent(event: Event) {
      const handlers = listeners.get(event.type) ?? new Set<EventListenerOrEventListenerObject>()
      for (const handler of handlers) {
        if (typeof handler === 'function') {
          handler(event)
        } else {
          handler.handleEvent(event)
        }
      }
      return true
    },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  } as unknown as Window & typeof globalThis

  const documentStub = {
    documentElement: {
      classList: {
        toggle() {
          return false
        },
      },
    },
  } as unknown as Document

  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: windowStub,
  })

  Object.defineProperty(globalThis, 'document', {
    configurable: true,
    value: documentStub,
  })

  return {
    window: windowStub,
    cleanup() {
      Object.defineProperty(globalThis, 'window', {
        configurable: true,
        value: previousWindow,
      })
      Object.defineProperty(globalThis, 'document', {
        configurable: true,
        value: previousDocument,
      })
    },
  }
}

export function renderToHtml(element: ReactElement) {
  return renderToStaticMarkup(element)
}
