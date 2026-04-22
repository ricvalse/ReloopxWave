import { PageHeader } from '@reloop/ui';

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Impostazioni" description="Configurazioni globali, API keys, webhook." />
      <div className="p-6 text-sm text-muted-foreground">
        TODO: gestione secrets (solo read), webhook health, feature flags (leggibili dal DB via Realtime).
      </div>
    </>
  );
}
