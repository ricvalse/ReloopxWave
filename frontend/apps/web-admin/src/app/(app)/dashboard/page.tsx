import { PageHeader } from '@reloop/ui';
import { AgencyDashboard } from '@/components/dashboard/agency-dashboard';

export default function DashboardPage() {
  return (
    <>
      <PageHeader
        title="Dashboard Agenzia"
        description="UC-12 — vista aggregata di tutti i merchant."
      />
      <AgencyDashboard />
    </>
  );
}
