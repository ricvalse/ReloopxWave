import { Suspense } from 'react';
import { PageHeader, SkeletonCard } from '@reloop/ui';
import { IntegrationsPanel } from '@/components/integrations/integrations-panel';

export default function IntegrationsPage() {
  return (
    <>
      <PageHeader
        title="Integrazioni"
        description="Stato connessioni GoHighLevel e WhatsApp Cloud."
      />
      <Suspense fallback={<div className="grid gap-4 p-6 md:grid-cols-2"><SkeletonCard /><SkeletonCard /></div>}>
        <IntegrationsPanel />
      </Suspense>
    </>
  );
}
