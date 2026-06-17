import { Suspense } from 'react';
import { PageHeader, SkeletonCard } from '@reloop/ui';
import { FlowsPanel } from '@/components/flussi/flows-panel';

export default function FlussiPage() {
  return (
    <>
      <PageHeader
        title="Flussi"
        description="Sequenze di messaggi automatici: per ogni passo scegli il template e la regola della finestra di 24h."
      />
      <Suspense fallback={<div className="space-y-4 p-6"><SkeletonCard /><SkeletonCard /></div>}>
        <FlowsPanel />
      </Suspense>
    </>
  );
}
