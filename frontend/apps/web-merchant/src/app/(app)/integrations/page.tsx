import { Suspense } from 'react';
import { PageHeader } from '@reloop/ui';
import { IntegrationsPanel } from '@/components/integrations/integrations-panel';

export default function IntegrationsPage() {
  return (
    <>
      <PageHeader
        title="Integrazioni"
        description="Stato connessioni GoHighLevel e WhatsApp Cloud."
      />
      <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Caricamento…</div>}>
        <IntegrationsPanel />
      </Suspense>
    </>
  );
}
