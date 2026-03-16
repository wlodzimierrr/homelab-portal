import { useCallback, useState } from 'react'
import {
  previewScaffold,
  submitScaffold,
  type ScaffoldPreviewFile,
  type ScaffoldServiceRequest,
  type ScaffoldSubmitResponse,
} from '@/lib/api'
import { cn } from '@/lib/utils'

type Step = 'basic' | 'template' | 'config' | 'preview' | 'success'

type TemplateId = 'python-fastapi' | 'static-nginx' | 'postgres' | 'mysql'

const DB_TEMPLATES: TemplateId[] = ['postgres', 'mysql']

function isDatabaseTemplate(template: TemplateId): boolean {
  return DB_TEMPLATES.includes(template)
}

interface WizardFormState {
  name: string
  description: string
  ownerEmail: string
  owner: string
  template: TemplateId
  imageRepo: string
  repoUrl: string
  namespace: string
  devHost: string
  prodHost: string
  publicHost: string
}

const EMPTY_FORM: WizardFormState = {
  name: '',
  description: '',
  ownerEmail: '',
  owner: '',
  template: 'python-fastapi',
  imageRepo: '',
  repoUrl: '',
  namespace: '',
  devHost: '',
  prodHost: '',
  publicHost: '',
}

const STEPS: Step[] = ['basic', 'template', 'config', 'preview']
const STEP_LABELS: Record<Step, string> = {
  basic: 'Basic info',
  template: 'Template',
  config: 'Configuration',
  preview: 'Review & confirm',
  success: 'Done',
}

interface Props {
  onClose: () => void
}

function StepIndicator({ currentStep }: { currentStep: Step }) {
  const currentIndex = STEPS.indexOf(currentStep)
  return (
    <ol className="mb-6 flex items-center gap-2 text-xs">
      {STEPS.map((step, index) => (
        <li key={step} className="flex items-center gap-2">
          <span
            className={cn(
              'flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold',
              index < currentIndex
                ? 'bg-emerald-500 text-white'
                : index === currentIndex
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground',
            )}
          >
            {index < currentIndex ? '✓' : index + 1}
          </span>
          <span
            className={cn(
              'hidden sm:inline',
              index === currentIndex ? 'font-medium' : 'text-muted-foreground',
            )}
          >
            {STEP_LABELS[step]}
          </span>
          {index < STEPS.length - 1 && <span className="text-muted-foreground">›</span>}
        </li>
      ))}
    </ol>
  )
}

function FieldLabel({ htmlFor, children, hint }: { htmlFor?: string; children: React.ReactNode; hint?: string }) {
  return (
    <label htmlFor={htmlFor} className="block space-y-1">
      <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{children}</span>
      {hint ? <span className="block text-xs text-muted-foreground">{hint}</span> : null}
    </label>
  )
}

function TextInput({
  id,
  value,
  onChange,
  placeholder,
  required,
}: {
  id: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  required?: boolean
}) {
  return (
    <input
      id={id}
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      required={required}
      className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
    />
  )
}

function BasicInfoStep({
  form,
  onChange,
}: {
  form: WizardFormState
  onChange: (patch: Partial<WizardFormState>) => void
}) {
  return (
    <div className="space-y-4">
      <div>
        <FieldLabel htmlFor="svc-name" hint="Lowercase kebab-case, e.g. my-service">
          Service name *
        </FieldLabel>
        <TextInput
          id="svc-name"
          value={form.name}
          onChange={(v) => onChange({ name: v.toLowerCase().replace(/[^a-z0-9-]/g, '-') })}
          placeholder="my-service"
          required
        />
      </div>
      <div>
        <FieldLabel htmlFor="svc-description">Description *</FieldLabel>
        <TextInput
          id="svc-description"
          value={form.description}
          onChange={(v) => onChange({ description: v })}
          placeholder="Short description of this service"
          required
        />
      </div>
      <div>
        <FieldLabel htmlFor="svc-owner-email">Owner email *</FieldLabel>
        <TextInput
          id="svc-owner-email"
          value={form.ownerEmail}
          onChange={(v) => onChange({ ownerEmail: v })}
          placeholder="you@example.com"
          required
        />
      </div>
      <div>
        <FieldLabel htmlFor="svc-owner" hint="Team or GitHub username (optional, defaults to email)">
          Owner label
        </FieldLabel>
        <TextInput
          id="svc-owner"
          value={form.owner}
          onChange={(v) => onChange({ owner: v })}
          placeholder="platform"
        />
      </div>
    </div>
  )
}

function TemplateStep({
  form,
  onChange,
}: {
  form: WizardFormState
  onChange: (patch: Partial<WizardFormState>) => void
}) {
  const options: { key: TemplateId; title: string; description: string }[] = [
    {
      key: 'python-fastapi',
      title: 'Python + FastAPI',
      description:
        'Backend API service on port 8000. Includes ServiceMonitor for Prometheus scraping (app-native observability).',
    },
    {
      key: 'static-nginx',
      title: 'Static site + Nginx',
      description:
        'Frontend or static asset server on port 80. Observability via ingress metrics (ingress-derived mode).',
    },
    {
      key: 'postgres',
      title: 'PostgreSQL 17',
      description:
        'Standalone PostgreSQL 17 database. StatefulSet with PVC, ClusterIP service, SOPS-encrypted credentials stub. No ingress — connects to other services in-cluster.',
    },
    {
      key: 'mysql',
      title: 'MySQL 8.0',
      description:
        'Standalone MySQL 8.0 database. StatefulSet with PVC, ClusterIP service, SOPS-encrypted credentials stub. No ingress — connects to other services in-cluster.',
    },
  ]

  return (
    <div className="space-y-3">
      {options.map((opt) => (
        <button
          key={opt.key}
          type="button"
          onClick={() => onChange({ template: opt.key })}
          className={cn(
            'w-full rounded-md border p-4 text-left transition-colors',
            form.template === opt.key
              ? 'border-primary bg-primary/5'
              : 'border-border hover:border-primary/50',
          )}
        >
          <p className="font-medium">{opt.title}</p>
          <p className="mt-1 text-sm text-muted-foreground">{opt.description}</p>
        </button>
      ))}
    </div>
  )
}

function ConfigStep({
  form,
  onChange,
}: {
  form: WizardFormState
  onChange: (patch: Partial<WizardFormState>) => void
}) {
  const isDb = isDatabaseTemplate(form.template)

  return (
    <div className="space-y-4">
      {!isDb && (
        <>
          <div>
            <FieldLabel htmlFor="svc-image-repo" hint="e.g. ghcr.io/wlodzimierrr/my-service (without tag)">
              Image repository *
            </FieldLabel>
            <TextInput
              id="svc-image-repo"
              value={form.imageRepo}
              onChange={(v) => onChange({ imageRepo: v })}
              placeholder="ghcr.io/org/my-service"
              required
            />
          </div>
          <div>
            <FieldLabel htmlFor="svc-repo-url">Source repository URL *</FieldLabel>
            <TextInput
              id="svc-repo-url"
              value={form.repoUrl}
              onChange={(v) => onChange({ repoUrl: v })}
              placeholder="https://github.com/org/my-service"
              required
            />
          </div>
        </>
      )}
      <div>
        <FieldLabel htmlFor="svc-namespace" hint={`Defaults to service name: ${form.name || '<name>'}`}>
          Kubernetes namespace
        </FieldLabel>
        <TextInput
          id="svc-namespace"
          value={form.namespace}
          onChange={(v) => onChange({ namespace: v })}
          placeholder={form.name || 'my-service'}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <FieldLabel htmlFor="svc-dev-host" hint={`Default: ${form.name || '<name>'}.dev.homelab.local`}>
            Dev ingress host
          </FieldLabel>
          <TextInput
            id="svc-dev-host"
            value={form.devHost}
            onChange={(v) => onChange({ devHost: v })}
            placeholder={`${form.name || 'my-service'}.dev.homelab.local`}
          />
        </div>
        <div>
          <FieldLabel htmlFor="svc-prod-host" hint={`Default: ${form.name || '<name>'}.homelab.local`}>
            Prod ingress host
          </FieldLabel>
          <TextInput
            id="svc-prod-host"
            value={form.prodHost}
            onChange={(v) => onChange({ prodHost: v })}
            placeholder={`${form.name || 'my-service'}.homelab.local`}
          />
        </div>
      </div>
      <div>
        <FieldLabel
          htmlFor="svc-public-host"
          hint={`External DNS hostname written to patch-ingress.yaml. Default: ${form.name || '<name>'}.<PUBLIC_BASE_DOMAIN>`}
        >
          Public hostname
        </FieldLabel>
        <TextInput
          id="svc-public-host"
          value={form.publicHost}
          onChange={(v) => onChange({ publicHost: v })}
          placeholder={`${form.name || 'my-service'}.example.com`}
        />
      </div>
    </div>
  )
}

function PreviewStep({
  files,
  loading,
  error,
  submitting,
  submitError,
  onSubmit,
}: {
  files: ScaffoldPreviewFile[]
  loading: boolean
  error: string
  submitting: boolean
  submitError: string
  onSubmit: () => void
}) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const selectedFile = files.find((f) => f.path === selectedPath) ?? files[0] ?? null

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading preview...</p>
  }
  if (error) {
    return (
      <div className="rounded-md border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-900 dark:text-rose-200">
        {error}
      </div>
    )
  }

  const created = files.filter((f) => f.changeType === 'create')
  const modified = files.filter((f) => f.changeType === 'modify')

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        {created.length} new files + {modified.length} modified files will be committed in a single PR.
      </p>

      <div className="grid gap-3 lg:grid-cols-[240px_1fr]">
        <div className="max-h-72 overflow-y-auto rounded-md border border-border lg:max-h-96">
          {modified.length > 0 && (
            <>
              <p className="sticky top-0 bg-muted px-2 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Modified
              </p>
              {modified.map((f) => (
                <button
                  key={f.path}
                  type="button"
                  onClick={() => setSelectedPath(f.path)}
                  className={cn(
                    'w-full truncate px-2 py-1.5 text-left text-xs',
                    selectedFile?.path === f.path
                      ? 'bg-primary/10 font-medium'
                      : 'hover:bg-muted',
                  )}
                >
                  {f.path}
                </button>
              ))}
            </>
          )}
          {created.length > 0 && (
            <>
              <p className="sticky top-0 bg-muted px-2 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Created
              </p>
              {created.map((f) => (
                <button
                  key={f.path}
                  type="button"
                  onClick={() => setSelectedPath(f.path)}
                  className={cn(
                    'w-full truncate px-2 py-1.5 text-left text-xs',
                    selectedFile?.path === f.path
                      ? 'bg-primary/10 font-medium'
                      : 'hover:bg-muted',
                  )}
                >
                  {f.path}
                </button>
              ))}
            </>
          )}
        </div>

        <div className="min-h-48 overflow-auto rounded-md border border-border bg-muted/30 p-3">
          {selectedFile ? (
            <>
              <p className="mb-2 text-xs font-medium text-muted-foreground">{selectedFile.path}</p>
              <pre className="whitespace-pre-wrap break-all text-xs leading-relaxed">
                {selectedFile.content}
              </pre>
            </>
          ) : (
            <p className="text-xs text-muted-foreground">Select a file to preview its content.</p>
          )}
        </div>
      </div>

      {submitError ? (
        <div className="rounded-md border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-900 dark:text-rose-200">
          {submitError}
        </div>
      ) : null}

      <button
        type="button"
        onClick={onSubmit}
        disabled={submitting}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {submitting ? 'Creating PR...' : 'Create PR'}
      </button>
    </div>
  )
}

function SuccessStep({ result, onClose }: { result: ScaffoldSubmitResponse; onClose: () => void }) {
  return (
    <div className="space-y-4">
      <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-4">
        <p className="font-medium text-emerald-900 dark:text-emerald-200">PR created successfully</p>
        <p className="mt-1 text-sm text-emerald-900 dark:text-emerald-200">
          Branch <code className="font-mono">{result.branchName}</code> — PR #{result.prNumber}
        </p>
      </div>
      <a
        href={result.prUrl}
        target="_blank"
        rel="noreferrer"
        className="block text-sm text-primary hover:underline"
      >
        Open PR #{result.prNumber} on GitHub →
      </a>
      <p className="text-xs text-muted-foreground">
        {result.filesCommitted.length} files committed. After merging and Argo sync, the service
        will appear in the services catalog.
      </p>
      <button
        type="button"
        onClick={onClose}
        className="rounded-md border border-border px-4 py-2 text-sm hover:bg-muted"
      >
        Close
      </button>
    </div>
  )
}

function validateBasic(form: WizardFormState): string {
  if (!form.name.trim()) return 'Service name is required.'
  if (!/^[a-z][a-z0-9-]{1,62}$/.test(form.name)) return 'Service name must be lowercase kebab-case (e.g. my-service).'
  if (!form.description.trim()) return 'Description is required.'
  if (!form.ownerEmail.trim()) return 'Owner email is required.'
  return ''
}

function validateConfig(form: WizardFormState): string {
  if (!isDatabaseTemplate(form.template)) {
    if (!form.imageRepo.trim()) return 'Image repository is required.'
    if (!form.repoUrl.trim()) return 'Source repository URL is required.'
  }
  return ''
}

export function ScaffoldServiceWizard({ onClose }: Props) {
  const [step, setStep] = useState<Step>('basic')
  const [form, setForm] = useState<WizardFormState>(EMPTY_FORM)
  const [validationError, setValidationError] = useState('')
  const [previewFiles, setPreviewFiles] = useState<ScaffoldPreviewFile[]>([])
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [submitResult, setSubmitResult] = useState<ScaffoldSubmitResponse | null>(null)

  const patchForm = useCallback((patch: Partial<WizardFormState>) => {
    setForm((prev) => ({ ...prev, ...patch }))
    setValidationError('')
  }, [])

  const buildPayload = (): ScaffoldServiceRequest => ({
    name: form.name.trim(),
    description: form.description.trim(),
    imageRepo: form.imageRepo.trim() || undefined,
    repoUrl: form.repoUrl.trim() || undefined,
    ownerEmail: form.ownerEmail.trim(),
    owner: form.owner.trim(),
    template: form.template,
    namespace: form.namespace.trim() || undefined,
    devHost: form.devHost.trim() || undefined,
    prodHost: form.prodHost.trim() || undefined,
    publicHost: form.publicHost.trim() || undefined,
  })

  const goNext = useCallback(async () => {
    setValidationError('')

    if (step === 'basic') {
      const err = validateBasic(form)
      if (err) { setValidationError(err); return }
      setStep('template')
    } else if (step === 'template') {
      setStep('config')
    } else if (step === 'config') {
      const err = validateConfig(form)
      if (err) { setValidationError(err); return }

      setPreviewLoading(true)
      setPreviewError('')
      setPreviewFiles([])
      setStep('preview')
      try {
        const result = await previewScaffold(buildPayload())
        setPreviewFiles(result.files)
      } catch (e) {
        setPreviewError(e instanceof Error ? e.message : 'Failed to load preview.')
      } finally {
        setPreviewLoading(false)
      }
    }
  }, [step, form])

  const goBack = useCallback(() => {
    setValidationError('')
    if (step === 'template') setStep('basic')
    else if (step === 'config') setStep('template')
    else if (step === 'preview') setStep('config')
  }, [step])

  const handleSubmit = useCallback(async () => {
    setSubmitting(true)
    setSubmitError('')
    try {
      const result = await submitScaffold(buildPayload())
      setSubmitResult(result)
      setStep('success')
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : 'Failed to create PR.')
    } finally {
      setSubmitting(false)
    }
  }, [form])

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 pt-16"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="w-full max-w-2xl rounded-lg border border-border bg-background shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-base font-semibold">New service</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
          >
            ✕
          </button>
        </div>

        <div className="px-6 py-5">
          {step !== 'success' && <StepIndicator currentStep={step} />}

          {step === 'basic' && <BasicInfoStep form={form} onChange={patchForm} />}
          {step === 'template' && <TemplateStep form={form} onChange={patchForm} />}
          {step === 'config' && <ConfigStep form={form} onChange={patchForm} />}
          {step === 'preview' && (
            <PreviewStep
              files={previewFiles}
              loading={previewLoading}
              error={previewError}
              submitting={submitting}
              submitError={submitError}
              onSubmit={handleSubmit}
            />
          )}
          {step === 'success' && submitResult && (
            <SuccessStep result={submitResult} onClose={onClose} />
          )}

          {validationError ? (
            <p className="mt-3 text-sm text-rose-600 dark:text-rose-400">{validationError}</p>
          ) : null}
        </div>

        {step !== 'preview' && step !== 'success' && (
          <div className="flex justify-between border-t border-border px-6 py-4">
            <button
              type="button"
              onClick={step === 'basic' ? onClose : goBack}
              className="rounded-md border border-border px-4 py-2 text-sm hover:bg-muted"
            >
              {step === 'basic' ? 'Cancel' : 'Back'}
            </button>
            <button
              type="button"
              onClick={() => void goNext()}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              {step === 'config' ? 'Preview' : 'Next'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
