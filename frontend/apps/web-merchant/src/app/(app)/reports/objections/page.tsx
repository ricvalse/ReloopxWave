import { PageHeader } from '@reloop/ui';
import { ObjectionReport } from '@/components/reports/objection-report';

export default function ObjectionsReportPage() {
  return (
    <>
      <PageHeader title="Report obiezioni" description="UC-13 — categorie e campioni recenti." />
      <div className="p-6">
        <ObjectionReport />
      </div>
    </>
  );
}
