// The scaffold wizard collects enough metadata to generate both app templates and
// GitOps catalog entries, so several fields reflect backend/workloads conventions.
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

type TemplateId = 'python-fastapi' | 'python-django' | 'python-flask' | 'static-nginx' | 'react' | 'nextjs' | 'vue' | 'wordpress' | 'node-express' | 'node-nestjs' | 'postgres' | 'mysql'

const DB_TEMPLATES: TemplateId[] = ['postgres', 'mysql']

function isDatabaseTemplate(template: TemplateId): boolean {
  return DB_TEMPLATES.includes(template)
}

function isWordPressTemplate(template: TemplateId): boolean {
  return template === 'wordpress'
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
  dbUsername: string
  dbPassword: string
  dbName: string
}

// The form state mirrors the scaffold API request closely so preview and submit
// share one source of truth as the user moves between steps.
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
  dbUsername: '',
  dbPassword: '',
  dbName: '',
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

// Template descriptions intentionally encode runtime and observability assumptions
// because the backend generates different manifests depending on the chosen stack.

interface TemplateOption {
  key: TemplateId
  title: string
  description: string
  runtime: string
  port: number
  observability: 'app-native' | 'ingress-derived' | 'no-http'
}

interface TemplateCategory {
  label: string
  templates: TemplateOption[]
}

const TEMPLATE_CATEGORIES: TemplateCategory[] = [
  {
    label: 'Backend',
    templates: [
      {
        key: 'python-fastapi',
        title: 'Python + FastAPI',
        description: 'Async Python API with Uvicorn. ServiceMonitor for Prometheus scraping.',
        runtime: 'Python',
        port: 8000,
        observability: 'app-native',
      },
      {
        key: 'python-django',
        title: 'Python + Django',
        description: 'Django with Gunicorn. Trailing-slash health endpoint convention.',
        runtime: 'Python',
        port: 8000,
        observability: 'app-native',
      },
      {
        key: 'python-flask',
        title: 'Python + Flask',
        description: 'Lightweight Flask API with Gunicorn.',
        runtime: 'Python',
        port: 5000,
        observability: 'app-native',
      },
      {
        key: 'node-express',
        title: 'Express.js',
        description: 'Node.js API with prom-client metrics.',
        runtime: 'Node.js',
        port: 3000,
        observability: 'app-native',
      },
      {
        key: 'node-nestjs',
        title: 'NestJS',
        description: 'TypeScript framework with built-in health module and structured layout.',
        runtime: 'Node.js / TS',
        port: 3000,
        observability: 'app-native',
      },
    ],
  },
  {
    label: 'Frontend',
    templates: [
      {
        key: 'react',
        title: 'React (Vite)',
        description: 'React SPA. Multi-stage Dockerfile (Node build, nginx serve).',
        runtime: 'Node.js / nginx',
        port: 80,
        observability: 'ingress-derived',
      },
      {
        key: 'nextjs',
        title: 'Next.js',
        description: 'SSR/ISR React framework. Node.js runtime with health API route.',
        runtime: 'Node.js',
        port: 3000,
        observability: 'app-native',
      },
      {
        key: 'vue',
        title: 'Vue (Vite)',
        description: 'Vue SPA. Multi-stage Dockerfile (Node build, nginx serve).',
        runtime: 'Node.js / nginx',
        port: 80,
        observability: 'ingress-derived',
      },
      {
        key: 'static-nginx',
        title: 'Static site + Nginx',
        description: 'Plain static assets served by nginx.',
        runtime: 'nginx',
        port: 80,
        observability: 'ingress-derived',
      },
    ],
  },
  {
    label: 'CMS',
    templates: [
      {
        key: 'wordpress',
        title: 'WordPress',
        description: 'Prebuilt WordPress image with bundled MySQL and persistent wp-content.',
        runtime: 'PHP / Apache',
        port: 80,
        observability: 'ingress-derived',
      },
    ],
  },
  {
    label: 'Database',
    templates: [
      {
        key: 'postgres',
        title: 'PostgreSQL 17',
        description: 'StatefulSet with PVC, ClusterIP service, SOPS-encrypted credentials.',
        runtime: 'PostgreSQL',
        port: 5432,
        observability: 'no-http',
      },
      {
        key: 'mysql',
        title: 'MySQL 8.0',
        description: 'StatefulSet with PVC, ClusterIP service, SOPS-encrypted credentials.',
        runtime: 'MySQL',
        port: 3306,
        observability: 'no-http',
      },
    ],
  },
]

function ObservabilityBadge({ mode }: { mode: string }) {
  const tone =
    mode === 'app-native'
      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
      : mode === 'no-http'
        ? 'bg-slate-500/10 text-slate-700 dark:text-slate-300'
        : 'bg-sky-500/10 text-sky-700 dark:text-sky-300'

  return (
    <span className={cn('inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium', tone)}>
      {mode}
    </span>
  )
}

function TemplateStep({
  form,
  onChange,
}: {
  form: WizardFormState
  onChange: (patch: Partial<WizardFormState>) => void
}) {
  const [search, setSearch] = useState('')
  const query = search.trim().toLowerCase()

  const filteredCategories = TEMPLATE_CATEGORIES.map((category) => ({
    ...category,
    templates: category.templates.filter(
      (t) =>
        !query ||
        t.title.toLowerCase().includes(query) ||
        t.description.toLowerCase().includes(query) ||
        t.runtime.toLowerCase().includes(query) ||
        category.label.toLowerCase().includes(query),
    ),
  })).filter((category) => category.templates.length > 0)

  return (
    <div className="space-y-4">
      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Filter templates..."
        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
      />
      {filteredCategories.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No templates match your search.</p>
      ) : null}
      {filteredCategories.map((category) => (
        <div key={category.label}>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {category.label}
          </h3>
          <div className="space-y-2">
            {category.templates.map((opt) => (
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
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium">{opt.title}</p>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                      {opt.runtime}
                    </span>
                    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                      :{opt.port}
                    </span>
                    <ObservabilityBadge mode={opt.observability} />
                  </div>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{opt.description}</p>
              </button>
            ))}
          </div>
        </div>
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
  const isWordPress = isWordPressTemplate(form.template)

  return (
    <div className="space-y-4">
      {!isDb && !isWordPress && (
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
      )}
      {!isDb && (
        <div>
          <FieldLabel htmlFor="svc-repo-url">
            Source repository URL *
          </FieldLabel>
          <TextInput
            id="svc-repo-url"
            value={form.repoUrl}
            onChange={(v) => onChange({ repoUrl: v })}
            placeholder="https://github.com/org/my-service"
            required
          />
        </div>
      )}
      {isWordPress && (
        <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          WordPress uses the prebuilt <code className="font-mono">wordpress:latest</code> image, so no application Dockerfile or CI workflow is generated.
        </p>
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
      {isDb && (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <FieldLabel htmlFor="svc-db-username" hint="Default: appuser">
                Database username
              </FieldLabel>
              <TextInput
                id="svc-db-username"
                value={form.dbUsername}
                onChange={(v) => onChange({ dbUsername: v })}
                placeholder="appuser"
              />
            </div>
            <div>
              <FieldLabel htmlFor="svc-db-name" hint="Default: appdb">
                Database name
              </FieldLabel>
              <TextInput
                id="svc-db-name"
                value={form.dbName}
                onChange={(v) => onChange({ dbName: v })}
                placeholder="appdb"
              />
            </div>
          </div>
          <div>
            <FieldLabel htmlFor="svc-db-password" hint="Written to SOPS-encrypted credentials secret">
              Database password
            </FieldLabel>
            <TextInput
              id="svc-db-password"
              value={form.dbPassword}
              onChange={(v) => onChange({ dbPassword: v })}
              placeholder="changeme"
            />
          </div>
        </>
      )}
      {isDb ? (
        <div>
          <FieldLabel htmlFor="svc-prod-host" hint={`Default: ${form.name || '<name>'}.homelab.local`}>
            Ingress host
          </FieldLabel>
          <TextInput
            id="svc-prod-host"
            value={form.prodHost}
            onChange={(v) => onChange({ prodHost: v })}
            placeholder={`${form.name || 'my-service'}.homelab.local`}
          />
        </div>
      ) : (
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
      )}
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
    if (!isWordPressTemplate(form.template) && !form.imageRepo.trim()) return 'Image repository is required.'
    if (!form.repoUrl.trim()) return 'Source repository URL is required.'
  }
  return ''
}

// The wizard keeps preview generation client-driven: validate locally, call the
// preview endpoint, then submit the same payload once the user confirms.
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
    setForm((prev) => {
      const next = { ...prev, ...patch }
      if (patch.template === 'wordpress') {
        next.imageRepo = 'wordpress:latest'
      }
      return next
    })
    setValidationError('')
  }, [])

  const buildPayload = (): ScaffoldServiceRequest => ({
    name: form.name.trim(),
    description: form.description.trim(),
    imageRepo: form.template === 'wordpress'
      ? form.imageRepo.trim() || 'wordpress:latest'
      : form.imageRepo.trim() || undefined,
    repoUrl: form.repoUrl.trim() || undefined,
    ownerEmail: form.ownerEmail.trim(),
    owner: form.owner.trim(),
    template: form.template,
    namespace: form.namespace.trim() || undefined,
    devHost: form.devHost.trim() || undefined,
    prodHost: form.prodHost.trim() || undefined,
    publicHost: form.publicHost.trim() || undefined,
    dbUsername: form.dbUsername.trim() || undefined,
    dbPassword: form.dbPassword.trim() || undefined,
    dbName: form.dbName.trim() || undefined,
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
