import { PageHeader } from '@reloop/ui';
import { MerchantDashboard } from '@/components/dashboard/merchant-dashboard';

export default function DashboardPage() {
  return (
    <>
      <PageHeader title="Dashboard" description="UC-11 — analytics del tuo bot." />
      <MerchantDashboard />
    </>
  );
}
