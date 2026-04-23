import { PageHeader } from '@reloop/ui';
import { AbTestingPanel } from '@/components/ab-testing/ab-testing-panel';

export default function AbTestingPage() {
  return (
    <>
      <PageHeader
        title="A/B testing"
        description="UC-09 — confronto varianti con significatività statistica."
      />
      <AbTestingPanel />
    </>
  );
}
