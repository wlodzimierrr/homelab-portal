import { AppLink } from '@/components/navigation/app-link'
import { PageShell } from '@/components/page-shell'
import { Button } from '@/components/ui/button'

const seedServices = ['auth-api', 'grafana', 'home-assistant']

export function ServicesPage() {
  return (
    <PageShell title="Services" description="Service catalog and runtime state.">
      <div className="flex flex-wrap gap-2">
        {seedServices.map((service) => (
          <Button key={service} asChild variant="outline">
            <AppLink to={`/services/${service}`}>{service}</AppLink>
          </Button>
        ))}
      </div>
    </PageShell>
  )
}
