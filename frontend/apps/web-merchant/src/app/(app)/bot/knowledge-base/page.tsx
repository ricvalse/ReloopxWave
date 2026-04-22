import { PageHeader } from '@reloop/ui';
import { KnowledgeBaseUploader } from '@/components/kb/kb-uploader';
import { KnowledgeBaseDocList } from '@/components/kb/kb-doc-list';

export default function KnowledgeBasePage() {
  return (
    <>
      <PageHeader
        title="Knowledge base"
        description="UC-07 — carica documenti. Verranno indicizzati automaticamente per il RAG del bot."
      />
      <div className="space-y-6 p-6">
        <KnowledgeBaseUploader />
        <KnowledgeBaseDocList />
      </div>
    </>
  );
}
