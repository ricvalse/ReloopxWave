import { ConversationsRoute } from '@/components/conversations/conversations-route';

export default async function ConversationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ConversationsRoute selectedId={id} />;
}
