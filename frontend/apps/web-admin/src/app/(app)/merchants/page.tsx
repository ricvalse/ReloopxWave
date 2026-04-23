import { PageHeader } from '@reloop/ui';
import { MerchantList } from '@/components/merchants/merchant-list';

export default function MerchantsPage() {
  return (
    <>
      <PageHeader
        title="Merchant"
        description="UC-10/11/12 — lista, onboarding, sospensione dei merchant del tenant."
      />
      <MerchantList />
    </>
  );
}
