import { PageHeader } from '@reloop/ui';

export default function TemplatesPage() {
  return (
    <>
      <PageHeader title="Template bot" description="UC-10 — default agenzia." />
      <div className="p-6 text-sm text-muted-foreground">
        TODO: BotTemplateEditor con preview prompt e toggle <code>locked_by_agency</code>.
      </div>
    </>
  );
}
