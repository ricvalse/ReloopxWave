import { PageHeader } from '@reloop/ui';

export default function IntegrationsPage() {
  return (
    <>
      <PageHeader title="Integrazioni" description="Stato connessioni GHL e WhatsApp." />
      <div className="p-6 text-sm text-muted-foreground">
        TODO: cards con stato OAuth (GHL) e numero verificato (Meta WhatsApp). Trigger riautenticazione
        via <code>/integrations/ghl/oauth/start</code>.
      </div>
    </>
  );
}
