import { InboxRoute } from '@/components/inbox/inbox-route';

export default async function InboxThreadPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <InboxRoute selectedId={id} />;
}
