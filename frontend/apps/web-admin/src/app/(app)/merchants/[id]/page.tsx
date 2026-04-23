import { PageHeader } from '@reloop/ui';
import { MerchantDetail } from '@/components/merchants/merchant-detail';

export default async function MerchantDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <>
      <PageHeader title="Dettaglio merchant" description="Config, stato, azioni amministrative." />
      <MerchantDetail merchantId={id} />
    </>
  );
}
