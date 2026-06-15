import { PageHeader } from '@reloop/ui';
import { AgencyGhlPanel } from '@/components/integrations/agency-ghl-panel';

export default function IntegrationsPage() {
  return (
    <>
      <PageHeader
        title="Integrazioni"
        description="Collega l'agenzia GoHighLevel e associa le location installate ai merchant."
      />
      <AgencyGhlPanel />
    </>
  );
}
