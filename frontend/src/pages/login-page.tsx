import { useState, type FormEvent } from 'react'
import { ErrorState } from '@/components/error-state'
import { AppLink } from '@/components/navigation/app-link'
import { login } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { Button } from '@/components/ui/button'

interface LoginPageProps {
  onLoginSuccess: () => void
}

export function LoginPage({ onLoginSuccess }: LoginPageProps) {
  const { setToken } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setIsLoading(true)

    try {
      const response = await login({ username, password })
      setToken(response.access_token)
      onLoginSuccess()
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : 'Unable to sign in'
      setError(message)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-4">
      <section className="w-full max-w-md rounded-xl border border-border bg-card p-8">
        <h1 className="text-2xl font-semibold">Portal Login</h1>
        <p className="mt-2 text-sm text-muted-foreground">Sign in with your portal credentials.</p>

        <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
          <label className="block space-y-1">
            <span className="text-sm">Username</span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              autoComplete="username"
              required
            />
          </label>
          <label className="block space-y-1">
            <span className="text-sm">Password</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              autoComplete="current-password"
              required
            />
          </label>

          {error ? <ErrorState message={error} /> : null}

          <Button type="submit" className="w-full" disabled={isLoading}>
            {isLoading ? 'Signing in...' : 'Sign in'}
          </Button>
        </form>

        <p className="mt-4 text-center text-xs text-muted-foreground">
          After login you will be redirected to <AppLink to="/dashboard">Dashboard</AppLink>.
        </p>
      </section>
    </main>
  )
}
