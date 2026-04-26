import { PageHeader } from '@reloop/ui';
import { InboxPanel } from '@/components/inbox/inbox-panel';

export default function InboxPage() {
  return (
    <>
      <PageHeader
        title="Inbox"
        description="Conversazioni di tutti i merchant del tenant — filtra per merchant o lasciale tutte. Realtime via Supabase."
      />
      <InboxPanel />
    </>
  );
}
