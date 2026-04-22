'use client';

import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type Doc = {
  id: string;
  title: string;
  source: string;
  status: string;
  chunk_count: number;
};

export function KnowledgeBaseDocList() {
  const { merchantId } = useMerchantId();

  const query = useQuery({
    enabled: !!merchantId,
    queryKey: ['kb-docs', merchantId],
    queryFn: async (): Promise<Doc[]> => {
      if (!merchantId) return [];
      const api = getApiClient();
      const { data, error } = await api.GET('/knowledge-base/{merchant_id}/docs' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return (data as Doc[]) ?? [];
    },
    refetchInterval: (data) =>
      Array.isArray(data) && data.some((d) => d.status === 'pending' || d.status === 'indexing')
        ? 3000
        : false,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Documenti</CardTitle>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <p className="text-sm text-muted-foreground">Caricamento…</p>
        ) : query.error ? (
          <p className="text-sm text-destructive">Errore nel caricare i documenti.</p>
        ) : !query.data?.length ? (
          <p className="text-sm text-muted-foreground">Nessun documento caricato.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-muted-foreground">
              <tr>
                <th className="py-2">Titolo</th>
                <th>Tipo</th>
                <th>Stato</th>
                <th className="text-right">Chunk</th>
              </tr>
            </thead>
            <tbody>
              {query.data.map((d) => (
                <tr key={d.id} className="border-t">
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
                  </td>
                  <td className="text-right">{d.chunk_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
