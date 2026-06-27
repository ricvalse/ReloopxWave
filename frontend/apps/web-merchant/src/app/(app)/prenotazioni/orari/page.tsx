import { PageHeader } from '@reloop/ui';
import { BusinessHoursPanel } from '@/components/prenotazioni/business-hours-panel';
import { BusinessClosuresPanel } from '@/components/prenotazioni/business-closures-panel';

export default function OrariPage() {
  return (
    <>
      <PageHeader
        title="Orari e disponibilità"
        description="Orari di apertura settimanali e chiusure eccezionali usati dal bot per le prenotazioni."
      />
      <BusinessHoursPanel />
      <BusinessClosuresPanel />
    </>
  );
}
