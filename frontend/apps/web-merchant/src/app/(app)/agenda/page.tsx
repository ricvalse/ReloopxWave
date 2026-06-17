import { PageHeader } from '@reloop/ui';
import { AgendaPanel } from '@/components/agenda/agenda-panel';

export default function AgendaPage() {
  return (
    <>
      <PageHeader
        title="Agenda"
        description="Appuntamenti sincronizzati con GoHighLevel — sposta o annulla direttamente da qui."
      />
      <div className="p-6">
        <AgendaPanel />
      </div>
    </>
  );
}
