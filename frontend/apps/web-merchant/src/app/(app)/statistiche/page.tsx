import { PageHeader } from '@reloop/ui';
import { StatistichePanel } from '@/components/statistiche/statistiche-panel';

export default function StatistichePage() {
  return (
    <>
      <PageHeader
        title="Statistiche"
        description="Scegli le bolle da mostrare, divise per profilo di conversazione."
      />
      <StatistichePanel />
    </>
  );
}
