import type { Dispatch, SetStateAction } from 'react'
import { Button } from '@/components/ui/button'
import type {
  ServiceConfigEntry,
  ServiceSetConfigResponse,
  UpdatePublicHostnameResponse,
} from '@/lib/api/admin'

interface AdminControlsSectionProps {
  configSupported: boolean
  publicHostEditMode: boolean
  setPublicHostEditMode: (value: boolean) => void
  publicHostValue: string
  setPublicHostValue: (value: string) => void
  publicHostSubmitting: boolean
  publicHostError: string
  publicHostResult: UpdatePublicHostnameResponse | null
  onSubmitPublicHostname: () => void
  configEnv: 'dev' | 'prod'
  setConfigEnv: (value: 'dev' | 'prod') => void
  configEntries: ServiceConfigEntry[]
  configLoading: boolean
  configError: string
  configSelectedValues: Record<string, string>
  setConfigSelectedValues: Dispatch<SetStateAction<Record<string, string>>>
  configSubmitting: boolean
  configSubmitError: string
  configSubmitResult: ServiceSetConfigResponse | null
  onSubmitConfigEdit: (key: string) => void
  onReloadConfig: (env: 'dev' | 'prod') => void
}

export function AdminControlsSection({
  configSupported,
  publicHostEditMode,
  setPublicHostEditMode,
  publicHostValue,
  setPublicHostValue,
  publicHostSubmitting,
  publicHostError,
  publicHostResult,
  onSubmitPublicHostname,
  configEnv,
  setConfigEnv,
  configEntries,
  configLoading,
  configError,
  configSelectedValues,
  setConfigSelectedValues,
  configSubmitting,
  configSubmitError,
  configSubmitResult,
  onSubmitConfigEdit,
  onReloadConfig,
}: AdminControlsSectionProps) {
  return (
    <>
      <section className="space-y-3 rounded-md border border-border bg-card p-4">
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <h2 className="text-sm font-semibold">Public hostname</h2>
            <p className="text-xs text-muted-foreground">
              External DNS name for the prod Ingress. Updating opens a GitOps PR.
            </p>
          </div>
          {!publicHostEditMode && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                setPublicHostEditMode(true)
              }}
            >
              Edit
            </Button>
          )}
        </div>
        {publicHostEditMode ? (
          <div className="space-y-3">
            <input
              type="text"
              value={publicHostValue}
              onChange={(e) => setPublicHostValue(e.target.value)}
              placeholder="my-service.example.com"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
            {publicHostError ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3">
                <p className="text-xs text-destructive">{publicHostError}</p>
              </div>
            ) : null}
            <div className="flex gap-2">
              <Button type="button" onClick={onSubmitPublicHostname} disabled={publicHostSubmitting || !publicHostValue.trim()}>
                {publicHostSubmitting ? 'Saving...' : 'Save'}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setPublicHostEditMode(false)
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm">{publicHostValue || <span className="text-muted-foreground">Not set</span>}</p>
            {publicHostResult ? (
              <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-950 dark:text-emerald-200">
                <p className="font-medium">PR created.</p>
                <a
                  href={publicHostResult.prUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-flex font-medium underline underline-offset-2"
                >
                  Open PR #{publicHostResult.prNumber} on GitHub →
                </a>
              </div>
            ) : null}
          </div>
        )}
      </section>

      {configSupported ? (
        <section className="space-y-3 rounded-md border border-border bg-card p-4">
          <div className="space-y-1">
            <h2 className="text-sm font-semibold">Runtime Config</h2>
            <p className="text-xs text-muted-foreground">
              Edit runtime ConfigMap values. Changes open a GitOps PR that triggers a pod restart on merge.
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Environment
            <select
              value={configEnv}
              onChange={(event) => setConfigEnv(event.target.value as 'dev' | 'prod')}
              className="rounded-md border border-border bg-background px-2 py-1 text-xs"
            >
              <option value="dev">dev</option>
              <option value="prod">prod</option>
            </select>
          </label>
          {configLoading ? (
            <p className="text-xs text-muted-foreground">Loading config...</p>
          ) : configError ? (
            <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
              <p className="text-xs text-amber-900 dark:text-amber-200">{configError}</p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-2"
                onClick={() => onReloadConfig(configEnv)}
              >
                Retry
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              {configEntries.map((entry) => (
                <article key={entry.key} className="space-y-3 rounded-md border border-border/70 bg-background/50 p-4">
                  <div>
                    <h3 className="text-sm font-semibold">{entry.key}</h3>
                    <p className="text-xs text-muted-foreground">
                      Current value:{' '}
                      <code className="rounded bg-muted px-1 py-0.5">{entry.value || '(unset)'}</code>
                    </p>
                  </div>
                  <label className="space-y-1">
                    <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      New value
                    </span>
                    <select
                      value={configSelectedValues[entry.key] ?? entry.value}
                      onChange={(event) =>
                        setConfigSelectedValues((prev) => ({ ...prev, [entry.key]: event.target.value }))
                      }
                      className="block rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                    >
                      {entry.allowedValues.map((v) => (
                        <option key={v} value={v}>
                          {v}
                        </option>
                      ))}
                    </select>
                  </label>
                  {configSubmitError ? (
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3">
                      <p className="text-xs text-destructive">{configSubmitError}</p>
                    </div>
                  ) : null}
                  {configSubmitResult?.configKey === entry.key ? (
                    <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-xs text-emerald-950 dark:text-emerald-200">
                      <p className="font-medium">
                        {configSubmitResult.status === 'noop'
                          ? (configSubmitResult.message ?? 'Value already set.')
                          : 'Config update PR created.'}
                      </p>
                      {configSubmitResult.gitPrUrl ? (
                        <a
                          href={configSubmitResult.gitPrUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-2 inline-flex font-medium underline underline-offset-2"
                        >
                          Open GitOps PR #{configSubmitResult.gitPrNumber ?? 'link'}
                        </a>
                      ) : null}
                    </div>
                  ) : null}
                  <Button type="button" onClick={() => onSubmitConfigEdit(entry.key)} disabled={configSubmitting}>
                    {configSubmitting ? 'Submitting...' : 'Update config'}
                  </Button>
                </article>
              ))}
              {configEntries.length === 0 ? (
                <p className="text-xs text-muted-foreground">No editable config keys for this service.</p>
              ) : null}
            </div>
          )}
          <div className="rounded-md border border-border/50 bg-muted/30 p-3">
            <p className="text-xs font-medium text-muted-foreground">Secrets editing</p>
            <p className="mt-1 text-xs text-muted-foreground">Not yet available in the portal UI.</p>
          </div>
        </section>
      ) : null}
    </>
  )
}
