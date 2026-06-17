'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Button,
  ButtonSpinner,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  SkeletonTable,
} from '@reloop/ui';
import { FileText, RefreshCw, Trash2 } from 'lucide-react';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type Doc = {
  id: string;
  title: string;
  source: string;
  status: string;
  chunk_count: number;
  status_detail?: string | null;
  last_error?: string | null;
};

export function KnowledgeBaseDocList() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();

  const query = useQuery({
    enabled: !!merchantId,
    queryKey: ['kb-docs', merchantId],
    queryFn: async (): Promise<Doc[]> => {
      if (!merchantId) return [];
      const api = getApiClient();
      const { data, error } = await api.GET('/knowledge-base/{merchant_id}/docs', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return (data as Doc[]) ?? [];
    },
    refetchInterval: (data) =>
      Array.isArray(data) && data.some((d) => d.status === 'pending' || d.status === 'indexing')
        ? 3000
        : false,
  });

  const reindex = useMutation({
    mutationFn: async (docId: string) => {
      const api = getApiClient();
      const { error } = await api.POST('/knowledge-base/{merchant_id}/docs/{doc_id}/reindex', {
        params: { path: { merchant_id: merchantId!, doc_id: docId } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-docs'] });
    },
  });

  const remove = useMutation({
    mutationFn: async (docId: string) => {
      const api = getApiClient();
      const { error } = await api.DELETE('/knowledge-base/{merchant_id}/docs/{doc_id}', {
        params: { path: { merchant_id: merchantId!, doc_id: docId } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-docs'] });
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Documenti</CardTitle>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <SkeletonTable rows={4} cols={4} />
        ) : query.error ? (
          <p className="text-sm text-destructive">Errore nel caricare i documenti.</p>
        ) : !query.data?.length ? (
          <EmptyState
            icon={FileText}
            title="Nessun documento"
            description="Carica un PDF, un DOCX o un link: il bot lo userà per rispondere ai clienti."
          />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-muted-foreground">
              <tr>
                <th className="py-2">Titolo</th>
                <th>Tipo</th>
                <th>Stato</th>
                <th className="text-right">Chunk</th>
                <th className="text-right">Azioni</th>
              </tr>
            </thead>
            <tbody>
              {query.data.map((d) => (
                <tr key={d.id} className="border-t align-top">
                  <td className="py-2">{d.title}</td>
                  <td className="uppercase">{d.source}</td>
                  <td>
                    <span
                      className={
                        d.status === 'indexed'
                          ? 'text-emerald-600'
                          : d.status === 'failed'
                            ? 'text-destructive'
                            : 'text-muted-foreground'
                      }
                    >
                      {d.status}
                    </span>
                    {d.last_error ? (
                      <p className="mt-0.5 text-xs text-destructive">{d.last_error}</p>
                    ) : d.status_detail ? (
                      <p className="mt-0.5 text-xs text-muted-foreground">{d.status_detail}</p>
                    ) : null}
                  </td>
                  <td className="text-right">{d.chunk_count}</td>
                  <td>
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        title="Re-indicizza"
                        disabled={
                          !merchantId ||
                          (reindex.isPending && reindex.variables === d.id) ||
                          d.status === 'pending' ||
                          d.status === 'indexing'
                        }
                        onClick={() => reindex.mutate(d.id)}
                      >
                        {reindex.isPending && reindex.variables === d.id ? (
                          <ButtonSpinner />
                        ) : (
                          <RefreshCw className="h-4 w-4" />
                        )}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        title="Elimina"
                        className="text-destructive hover:text-destructive"
                        disabled={!merchantId || (remove.isPending && remove.variables === d.id)}
                        onClick={() => {
                          if (
                            window.confirm(
                              `Eliminare "${d.title}"? L'azione è definitiva e rimuove i relativi chunk.`,
                            )
                          ) {
                            remove.mutate(d.id);
                          }
                        }}
                      >
                        {remove.isPending && remove.variables === d.id ? (
                          <ButtonSpinner />
                        ) : (
                          <Trash2 className="h-4 w-4" />
                        )}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {reindex.error ? (
          <p className="mt-3 text-sm text-destructive">
            Errore nella re-indicizzazione del documento.
          </p>
        ) : null}
        {remove.error ? (
          <p className="mt-3 text-sm text-destructive">
            Errore nell&apos;eliminazione del documento.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
