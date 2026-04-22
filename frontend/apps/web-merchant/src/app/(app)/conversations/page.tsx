import { PageHeader } from '@reloop/ui';

export default function ConversationsPage() {
  return (
    <>
      <PageHeader title="Conversazioni" description="Storico thread WhatsApp." />
      <div className="p-6 text-sm text-muted-foreground">
        TODO: ConversationViewer (threaded, sentiment badge, lead score). Letture dirette via
        Supabase con RLS — niente round-trip al backend.
      </div>
    </>
  );
}
