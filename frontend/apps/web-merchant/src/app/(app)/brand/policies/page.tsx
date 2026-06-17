import { PageHeader } from '@reloop/ui';
import { StorePoliciesPanel } from '@/components/brand/store-policies-panel';

export default function PoliciesPage() {
  return (
    <>
      <PageHeader
        title="Policy del negozio"
        description="Spedizioni, resi, pagamenti e garanzie. Il bot le usa per rispondere alle domande dei clienti."
      />
      <StorePoliciesPanel />
    </>
  );
}
