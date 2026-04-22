import { PageHeader } from '@reloop/ui';

export default function BotConfigPage() {
  return (
    <>
      <PageHeader
        title="Configurazione bot"
        description="Prompt, tono, regole. Tutti i parametri seguono la cascata merchant → agenzia → system."
      />
      <div className="p-6 text-sm text-muted-foreground">
        TODO: form react-hook-form + zod sullo schema <code>BotConfigSchema</code>, con badge
        Inherited/Customized/Locked accanto a ogni campo (vedi sez. 9.5).
      </div>
    </>
  );
}
