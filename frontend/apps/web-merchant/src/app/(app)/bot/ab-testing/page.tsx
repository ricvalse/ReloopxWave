import { PageHeader } from '@reloop/ui';

export default function ABTestingPage() {
  return (
    <>
      <PageHeader title="A/B testing" description="UC-09 — split percentuale tra varianti bot." />
      <div className="p-6 text-sm text-muted-foreground">
        TODO: ABTestSplitConfig + tabella metriche per variante con significatività statistica.
      </div>
    </>
  );
}
