import { PageHeader } from '@reloop/ui';
import { BrandInfoPanel } from '@/components/brand/brand-info-panel';

export default function BrandInfoPage() {
  return (
    <>
      <PageHeader
        title="Informazioni brand"
        description="Chi sei e cosa offri. Il bot parla a nome della tua attività usando questi dati."
      />
      <BrandInfoPanel />
    </>
  );
}
