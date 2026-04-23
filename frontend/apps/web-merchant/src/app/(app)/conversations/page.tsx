import { PageHeader } from '@reloop/ui';
import { ConversationsPanel } from '@/components/conversations/conversations-panel';

export default function ConversationsPage() {
  return (
    <>
      <PageHeader
        title="Conversazioni"
        description="Storico thread WhatsApp. Letture dirette via Supabase con RLS + Realtime."
      />
      <ConversationsPanel />
    </>
  );
}
