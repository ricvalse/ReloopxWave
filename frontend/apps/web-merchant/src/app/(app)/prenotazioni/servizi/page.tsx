import { PageHeader } from '@reloop/ui';
import { ServicesPanel } from '@/components/prenotazioni/services-panel';

export default function ServiziPage() {
  return (
    <>
      <PageHeader
        title="Servizi"
        description="Configura i servizi prenotabili: durata, prezzo e calendario GHL."
      />
      <ServicesPanel />
    </>
  );
}
