import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

function createStorage() {
  const values = new Map<string, string>()
  return {
    getItem(key: string) {
      return values.has(key) ? values.get(key)! : null
    },
    setItem(key: string, value: string) {
      values.set(key, value)
    },
    removeItem(key: string) {
      values.delete(key)
    },
    clear() {
      values.clear()
    },
  }
}

class TestEvent {
  type: string

  constructor(type: string) {
    this.type = type
  }
}

class TestCustomEvent<T = unknown> extends TestEvent {
  detail: T

  constructor(type: string, init?: { detail?: T }) {
    super(type)
    this.detail = init?.detail as T
  }
}

class TestPopStateEvent extends TestEvent {}

export function installTestWindow(pathname = '/dashboard', token = 'dev-static-token') {
  const listeners = new Map<string, Set<(event: Event) => void>>()
  const localStorage = createStorage()
  const sessionStorage = createStorage()

  if (token) {
    localStorage.setItem('portal-auth-token', token)
  }

  const location = {
    pathname,
    hostname: 'portal.homelab.local',
    host: 'portal.homelab.local',
    origin: 'http://portal.homelab.local',
  }
  const windowObject = {
    location,
    history: {
      pushState: (_state: unknown, _title: string, nextPath: string) => {
        location.pathname = nextPath
      },
      replaceState: (_state: unknown, _title: string, nextPath: string) => {
        location.pathname = nextPath
      },
    },
    localStorage,
    sessionStorage,
    addEventListener(type: string, handler: (event: Event) => void) {
      const set = listeners.get(type) ?? new Set()
      set.add(handler)
      listeners.set(type, set)
    },
    removeEventListener(type: string, handler: (event: Event) => void) {
      listeners.get(type)?.delete(handler)
    },
    dispatchEvent(event: Event) {
      for (const handler of listeners.get(event.type) ?? []) {
        handler(event)
      }
      return true
    },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
  }

  Object.assign(globalThis, {
    window: windowObject,
    document: {
      documentElement: {
        classList: {
          toggle() {},
          add() {},
          remove() {},
        },
      },
    },
    Event: TestEvent,
    CustomEvent: TestCustomEvent,
    PopStateEvent: TestPopStateEvent,
  })

  return windowObject
}

export function render(element: ReactElement) {
  return renderToStaticMarkup(element)
}
