import { PageHeader } from '@reloop/ui';
import { PlaygroundChat } from '@/components/playground/playground-chat';

export default function PlaygroundPage() {
  return (
    <>
      <PageHeader
        title="Playground"
        description="UC-08 — prova il bot senza inviare messaggi reali. I turni non vengono salvati."
      />
      <div className="p-6">
        <PlaygroundChat />
      </div>
    </>
  );
}
