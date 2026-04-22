import { PageHeader } from '@reloop/ui';

export default function MerchantsPage() {
  return (
    <>
      <PageHeader
        title="Merchant"
        description="Lista, creazione, sospensione. UC-10, UC-11, UC-12."
      />
      <div className="p-6 text-sm text-muted-foreground">
        TODO: MerchantTable con filtri, ranking, drill-down verso <code>/merchants/[id]</code>.
      </div>
    </>
  );
}
