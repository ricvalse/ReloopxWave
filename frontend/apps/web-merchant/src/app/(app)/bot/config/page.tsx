import { PageHeader } from '@reloop/ui';
import { BotConfigPanel } from '@/components/bot-config/bot-config-panel';

export default function BotConfigPage() {
  return (
    <>
      <PageHeader
        title="Configurazione bot"
        description="Cascata merchant → agenzia → sistema. Le chiavi bloccate non sono sovrascrivibili."
      />
      <BotConfigPanel />
    </>
  );
}
