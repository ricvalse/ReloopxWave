import { PageHeader } from '@reloop/ui';
import { AutomazioniPanel } from '@/components/automazioni/automazioni-panel';

export default function AutomazioniPage() {
  return (
    <>
      <PageHeader
        title="Automazioni"
        description="La lavagnetta: crea flussi automatici collegando un trigger a condizioni e azioni."
      />
      <AutomazioniPanel />
    </>
  );
}
