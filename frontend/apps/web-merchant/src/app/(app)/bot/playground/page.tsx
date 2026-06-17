import { PageHeader } from '@reloop/ui';
import { PlaygroundChat } from '@/components/playground/playground-chat';

export default function PlaygroundPage() {
  return (
    <>
      <PageHeader
        title="Playground"
        description="Prova come risponderebbe il bot ai messaggi WhatsApp: stesso prompt, stesse impostazioni e stesse azioni del sistema reale, ma in simulazione (dry-run). Nessun messaggio inviato, nessun dato scritto."
      />
      <div className="p-6">
        <PlaygroundChat />
      </div>
    </>
  );
}
