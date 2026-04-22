import { PageHeader } from '@reloop/ui';

export default async function MerchantDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <PageHeader title={`Merchant ${id}`} description="Config, analytics, conversazioni." />
      <div className="p-6 text-sm text-muted-foreground">
        TODO: MerchantDetailDrawer + tab config/analytics/conversations.
      </div>
    </>
  );
}
