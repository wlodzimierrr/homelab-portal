import type { Dispatch, SetStateAction } from 'react'
import type {
  ServiceConfigEntry,
  ServiceSetConfigResponse,
  UpdatePublicHostnameResponse,
} from '@/lib/api/admin'
import { AdminControlsSection } from '@/features/service-details/components/admin-controls-section'
import { AdoptServiceSection } from './adopt-service-section'

interface ServiceSettingsSectionsProps {
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
  adoptSupported: boolean
  adoptProjectId: string
  setAdoptProjectId: (value: string) => void
  adoptSubmitting: boolean
  adoptError: string
  adoptResult: { status: string; message: string; prUrl?: string } | null
  onSubmitAdopt: () => void
}

export function ServiceSettingsSections({
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
  adoptSupported,
  adoptProjectId,
  setAdoptProjectId,
  adoptSubmitting,
  adoptError,
  adoptResult,
  onSubmitAdopt,
}: ServiceSettingsSectionsProps) {
  return (
    <>
      <AdminControlsSection
        configSupported={configSupported}
        publicHostEditMode={publicHostEditMode}
        setPublicHostEditMode={setPublicHostEditMode}
        publicHostValue={publicHostValue}
        setPublicHostValue={setPublicHostValue}
        publicHostSubmitting={publicHostSubmitting}
        publicHostError={publicHostError}
        publicHostResult={publicHostResult}
        onSubmitPublicHostname={onSubmitPublicHostname}
        configEnv={configEnv}
        setConfigEnv={setConfigEnv}
        configEntries={configEntries}
        configLoading={configLoading}
        configError={configError}
        configSelectedValues={configSelectedValues}
        setConfigSelectedValues={setConfigSelectedValues}
        configSubmitting={configSubmitting}
        configSubmitError={configSubmitError}
        configSubmitResult={configSubmitResult}
        onSubmitConfigEdit={onSubmitConfigEdit}
        onReloadConfig={onReloadConfig}
      />

      {adoptSupported ? (
        <AdoptServiceSection
          projectId={adoptProjectId}
          setProjectId={setAdoptProjectId}
          submitting={adoptSubmitting}
          error={adoptError}
          result={adoptResult}
          onSubmit={onSubmitAdopt}
        />
      ) : null}
    </>
  )
}
