import { AppLink } from '@/components/navigation/app-link'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { PageShell } from '@/components/page-shell'
import { Button } from '@/components/ui/button'
import { useServiceOverview } from '@/features/service-details/use-service-overview'
import { ServiceSettingsSections } from './components/settings-sections'
import { useServiceSettings } from './use-service-settings'

interface ServiceSettingsPageProps {
  serviceId: string
}

export function ServiceSettingsPage({ serviceId }: ServiceSettingsPageProps) {
  const {
    decodedServiceId,
    overview,
    projectContext,
    capabilities,
    isLoading,
    error,
    loadOverview,
  } = useServiceOverview(serviceId)
  const settings = useServiceSettings({
    serviceId: decodedServiceId,
    initialPublicHost: overview?.publicHost,
    capabilities,
    refreshOverview: loadOverview,
  })

  return (
    <PageShell
      title={`Settings: ${decodedServiceId || 'unknown'}`}
      description="Runtime config, public hostname, catalog-linking, and decommission actions for this service."
    >
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
          <div className="flex items-center gap-2">
            <span className="rounded-md bg-primary/10 px-2 py-1 text-xs font-medium text-primary">
              Settings
            </span>
            {projectContext?.projectName ? (
              <span className="rounded-full border border-border px-2 py-1 text-xs text-muted-foreground">
                Project: {projectContext.projectName}
              </span>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="outline">
              <AppLink to={`/services/${encodeURIComponent(decodedServiceId)}`}>
                Back to overview
              </AppLink>
            </Button>
            <Button asChild variant="outline">
              <AppLink to={`/services/${encodeURIComponent(decodedServiceId)}/deployments`}>
                View deployments
              </AppLink>
            </Button>
          </div>
        </div>

        {isLoading ? <LoadingState label="Loading service settings..." rows={3} /> : null}
        {!isLoading && error ? <ErrorState message={error} onRetry={() => void loadOverview()} /> : null}

        {!isLoading && !error ? (
          <ServiceSettingsSections
              serviceId={decodedServiceId}
              configSupported={settings.configSupported}
              publicHostEditMode={settings.publicHostEditMode}
              setPublicHostEditMode={settings.setPublicHostEditMode}
              publicHostValue={settings.publicHostValue}
              setPublicHostValue={settings.setPublicHostValue}
              publicHostSubmitting={settings.publicHostSubmitting}
              publicHostError={settings.publicHostError}
              publicHostResult={settings.publicHostResult}
              onSubmitPublicHostname={() => void settings.submitPublicHostname()}
              configEnv={settings.configEnv}
              setConfigEnv={settings.setConfigEnv}
              configEntries={settings.configEntries}
              configLoading={settings.configLoading}
              configError={settings.configError}
              configSelectedValues={settings.configSelectedValues}
              setConfigSelectedValues={settings.setConfigSelectedValues}
              configSubmitting={settings.configSubmitting}
              configSubmitError={settings.configSubmitError}
              configSubmitResult={settings.configSubmitResult}
              onSubmitConfigEdit={(key) => void settings.submitConfigEdit(key)}
              onReloadConfig={(env) => void settings.loadConfig(env)}
              adoptSupported={settings.adoptSupported}
              adoptProjectId={settings.adoptProjectId}
              setAdoptProjectId={settings.setAdoptProjectId}
              adoptSubmitting={settings.adoptSubmitting}
              adoptError={settings.adoptError}
              adoptResult={settings.adoptResult}
              onSubmitAdopt={() => void settings.submitAdopt()}
              decommissionMode={capabilities.decommissionMode}
              decommissionReason={capabilities.decommissionReason}
              decommissionConfirmation={settings.decommissionConfirmation}
              setDecommissionConfirmation={settings.setDecommissionConfirmation}
              decommissionSubmitting={settings.decommissionSubmitting}
              decommissionError={settings.decommissionError}
              decommissionResult={settings.decommissionResult}
              onSubmitDecommission={() => void settings.submitDecommission()}
            />
        ) : null}
      </div>
    </PageShell>
  )
}
