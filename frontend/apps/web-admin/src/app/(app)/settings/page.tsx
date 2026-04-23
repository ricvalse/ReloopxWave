import { PageHeader } from '@reloop/ui';
import { SettingsPanel } from '@/components/settings/settings-panel';

export default function SettingsPage() {
  return (
    <>
      <PageHeader
        title="Impostazioni"
        description="Informazioni tenant, team, webhook health."
      />
      <SettingsPanel />
    </>
  );
}
