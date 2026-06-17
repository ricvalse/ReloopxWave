import { Suspense } from 'react';
import { PageHeader, SkeletonCard } from '@reloop/ui';
import { TemplatesPanel } from '@/components/whatsapp-templates/templates-panel';

export default function WhatsAppTemplatesPage() {
  return (
    <>
      <PageHeader
        title="Template WhatsApp"
        description="Modelli di messaggio approvati da Meta, necessari per scrivere ai contatti fuori dalla finestra di 24h."
      />
      <Suspense fallback={<div className="space-y-4 p-6"><SkeletonCard /><SkeletonCard /></div>}>
        <TemplatesPanel />
      </Suspense>
    </>
  );
}
