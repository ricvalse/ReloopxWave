import { PageHeader } from '@reloop/ui';
import { FaqPanel } from '@/components/brand/faq-panel';

export default function FaqPage() {
  return (
    <>
      <PageHeader
        title="Domande frequenti"
        description="Coppie domanda/risposta che il bot usa per rispondere come faresti tu."
      />
      <FaqPanel />
    </>
  );
}
