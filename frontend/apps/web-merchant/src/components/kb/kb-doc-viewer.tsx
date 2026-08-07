'use client';

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  EmptyState,
  SkeletonText,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@reloop/ui';
import { Download, ExternalLink, FileText } from 'lucide-react';
import { getApiClient } from '@/lib/api';

export type KbDoc = {
  id: string;
  title: string;
  source: string;
  status: string;
  chunk_count: number;
};

type DocView = {
  kind: 'file' | 'url';
  url: string;
  mime?: string | null;
  filename?: string | null;
  expires_at?: string | null;
};

type Chunk = {
  id: string;
  chunk_index: number;
  content: string;
  tokens: number;
};

/** Quanti chunk per pagina. Il backend limita comunque a 200. */
const CHUNK_PAGE_SIZE = 50;

/** Il browser rende inline solo questi; il DOCX si può solo scaricare. */
const INLINE_MIMES = ['application/pdf', 'text/plain'];

/**
 * L'URL è firmato lato server per 1h (`_VIEW_URL_TTL_S`). Teniamo la cache
 * appena sotto, come per i media WhatsApp, così non serviamo mai un link già
 * scaduto.
 */
const SIGNED_URL_STALE_MS = 55 * 60 * 1000;

/**
 * Anteprima di un documento della knowledge base.
 *
 * Due viste, perché non sono la stessa cosa: il file originale (com'è stato
 * caricato) e il testo effettivamente indicizzato (quello che il bot legge,
 * dopo estrazione e normalizzazione). Un DOCX con tabelle, per dire, mostra
 * nell'originale righe che nei chunk non ci sono.
 */
export function KnowledgeBaseDocViewer({
  merchantId,
  doc,
  onClose,
}: {
  merchantId: string | null;
  doc: KbDoc | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState('file');
  const [page, setPage] = useState(0);

  useEffect(() => {
    setTab('file');
    setPage(0);
  }, [doc?.id]);

  const enabled = !!merchantId && !!doc;

  const view = useQuery({
    enabled,
    queryKey: ['kb-doc-view', doc?.id],
    staleTime: SIGNED_URL_STALE_MS,
    retry: false,
    queryFn: async (): Promise<DocView> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/knowledge-base/{merchant_id}/docs/{doc_id}/view', {
        params: { path: { merchant_id: merchantId!, doc_id: doc!.id } },
      });
      if (error) throw new Error(errorCode(error));
      return data as DocView;
    },
  });

  const chunks = useQuery({
    enabled: enabled && tab === 'text',
    queryKey: ['kb-doc-chunks', doc?.id, page],
    queryFn: async (): Promise<Chunk[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/knowledge-base/{merchant_id}/docs/{doc_id}/chunks', {
        params: {
          path: { merchant_id: merchantId!, doc_id: doc!.id },
          query: { limit: CHUNK_PAGE_SIZE, offset: page * CHUNK_PAGE_SIZE },
        },
      });
      if (error) throw new Error(errorCode(error));
      return (data as Chunk[]) ?? [];
    },
  });

  const totalPages = doc ? Math.max(1, Math.ceil(doc.chunk_count / CHUNK_PAGE_SIZE)) : 1;

  return (
    <Dialog
      open={!!doc}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      {doc ? (
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle className="pr-8">{doc.title}</DialogTitle>
          </DialogHeader>

          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="outline">{doc.source.toUpperCase()}</Badge>
            <span>
              {doc.chunk_count} {doc.chunk_count === 1 ? 'chunk indicizzato' : 'chunk indicizzati'}
            </span>
          </div>

          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="file">Documento</TabsTrigger>
              <TabsTrigger value="text">Testo indicizzato</TabsTrigger>
            </TabsList>

            <TabsContent value="file">
              {view.isLoading ? (
                <SkeletonText lines={3} />
              ) : view.error ? (
                <NoFileNotice
                  code={view.error instanceof Error ? view.error.message : 'unknown'}
                  onShowText={() => setTab('text')}
                />
              ) : view.data?.kind === 'url' ? (
                <div className="space-y-3 rounded-md border p-4">
                  <p className="text-sm">
                    Documento indicizzato da un link esterno: il bot ha letto il contenuto della
                    pagina al momento dell&apos;indicizzazione.
                  </p>
                  <p className="break-all text-xs text-muted-foreground">{view.data.url}</p>
                  <a href={view.data.url} target="_blank" rel="noopener noreferrer">
                    <Button variant="outline" size="sm">
                      <ExternalLink className="h-4 w-4" />
                      Apri il link
                    </Button>
                  </a>
                </div>
              ) : view.data ? (
                <FilePreview view={view.data} title={doc.title} />
              ) : null}
            </TabsContent>

            <TabsContent value="text">
              {chunks.isLoading ? (
                <SkeletonText lines={6} />
              ) : chunks.error ? (
                <p className="text-sm text-destructive">
                  Errore nel caricare il testo indicizzato.
                </p>
              ) : !chunks.data?.length ? (
                <EmptyState
                  icon={FileText}
                  title="Nessun testo indicizzato"
                  description={
                    doc.status === 'indexed'
                      ? 'Il documento risulta indicizzato ma non ha chunk: prova a re-indicizzarlo.'
                      : "L'indicizzazione non è ancora conclusa. Riprova tra qualche istante."
                  }
                />
              ) : (
                <div className="space-y-3">
                  <p className="text-xs text-muted-foreground">
                    È il testo che il bot legge davvero. I chunk si sovrappongono di ~200 caratteri:
                    è voluto, evita di spezzare le frasi a metà.
                  </p>
                  <div className="max-h-[50vh] space-y-3 overflow-y-auto pr-1">
                    {chunks.data.map((c) => (
                      <div key={c.id} className="rounded-md border p-3">
                        <div className="mb-1.5 flex items-center justify-between text-xs text-muted-foreground">
                          <span>Chunk {c.chunk_index + 1}</span>
                          <span>{c.tokens} token</span>
                        </div>
                        <p className="whitespace-pre-wrap break-words text-sm">{c.content}</p>
                      </div>
                    ))}
                  </div>
                  {totalPages > 1 ? (
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={page === 0}
                        onClick={() => setPage((p) => Math.max(0, p - 1))}
                      >
                        Precedenti
                      </Button>
                      <span>
                        Pagina {page + 1} di {totalPages}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={page + 1 >= totalPages}
                        onClick={() => setPage((p) => p + 1)}
                      >
                        Successivi
                      </Button>
                    </div>
                  ) : null}
                </div>
              )}
            </TabsContent>
          </Tabs>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function FilePreview({ view, title }: { view: DocView; title: string }) {
  const inline = INLINE_MIMES.includes(view.mime ?? '');
  // L'attributo `download` viene ignorato sugli URL cross-origin (e quello
  // firmato punta a supabase.co), quindi da solo non scarica: naviga, portando
  // via la SPA. È Supabase a decidere, col parametro `download`, di rispondere
  // `Content-Disposition: attachment`. `target="_blank"` è la cintura di
  // sicurezza se il parametro non venisse onorato.
  const downloadUrl = view.filename
    ? `${view.url}${view.url.includes('?') ? '&' : '?'}download=${encodeURIComponent(view.filename)}`
    : view.url;
  return (
    <div className="space-y-3">
      {inline ? (
        <iframe
          src={view.url}
          title={title}
          className="h-[55vh] w-full rounded-md border bg-muted"
        />
      ) : (
        <div className="flex items-center gap-3 rounded-md border p-4 text-sm">
          <FileText className="h-5 w-5 shrink-0 text-muted-foreground" />
          <span className="text-muted-foreground">
            Il browser non può mostrare questo formato in anteprima. Scarica il file per aprirlo, o
            passa a &quot;Testo indicizzato&quot; per vedere cosa ha letto il bot.
          </span>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <a href={view.url} target="_blank" rel="noopener noreferrer">
          <Button variant="outline" size="sm">
            <ExternalLink className="h-4 w-4" />
            Apri in una nuova scheda
          </Button>
        </a>
        <a
          href={downloadUrl}
          download={view.filename ?? undefined}
          target="_blank"
          rel="noopener noreferrer"
        >
          <Button variant="outline" size="sm">
            <Download className="h-4 w-4" />
            Scarica
          </Button>
        </a>
      </div>
    </div>
  );
}

/** Un doc senza file (URL rotto, corpus sintetico, oggetto sparito dal bucket). */
function NoFileNotice({ code, onShowText }: { code: string; onShowText: () => void }) {
  const message =
    code === 'kb_doc_has_no_file'
      ? 'Questo documento non ha un file allegato: esiste solo come testo indicizzato.'
      : code === 'kb_file_unavailable'
        ? 'Il file non è più disponibile nello storage. Il testo indicizzato resta consultabile.'
        : 'Impossibile aprire il documento.';
  return (
    <div className="space-y-3 rounded-md border p-4">
      <p className="text-sm text-muted-foreground">{message}</p>
      <Button variant="outline" size="sm" onClick={onShowText}>
        Vedi il testo indicizzato
      </Button>
    </div>
  );
}

/** L'API risponde `{error: {code, message}}`; ci basta il code per ramificare. */
function errorCode(error: unknown): string {
  if (error && typeof error === 'object' && 'error' in error) {
    const inner = (error as { error?: { code?: string } }).error;
    if (inner?.code) return inner.code;
  }
  return 'unknown';
}
