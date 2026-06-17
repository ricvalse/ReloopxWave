import { PageHeader } from '@reloop/ui';
import { ProductCatalogPanel } from '@/components/brand/product-catalog-panel';

export default function CatalogPage() {
  return (
    <>
      <PageHeader
        title="Catalogo prodotti"
        description="I prodotti che il bot può proporre e citare nelle conversazioni."
      />
      <ProductCatalogPanel />
    </>
  );
}
