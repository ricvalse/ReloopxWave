'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { Trash2 } from 'lucide-react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type Correction = components['schemas']['CorrectionOut'];

export function CorrectionsPanel() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['corrections', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<Correction[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/catalog/{merchant_id}/corrections', {
        params: { path: { merchant_id: merchantId! } },
      });
      if (error) throw new Error(apiErrorMessage(error));
      return (data as Correction[]) ?? [];
    },
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      if (!merchantId) throw new Error('Merchant context mancante');
      const api = getApiClient();
      const { error } = await api.DELETE('/catalog/{merchant_id}/corrections/{correction_id}', {
        params: { path: { merchant_id: merchantId, correction_id: id } },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['corrections', merchantId] });
    },
  });

  const corrections = query.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-sm">
          Correzioni salvate
          {corrections.length > 0 ? (
            <Badge variant="secondary">{corrections.length}</Badge>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        <p className="text-muted-foreground">
          Le risposte che correggi qui vengono reiniettate come regole quando il cliente fa una
          domanda simile.
        </p>
        {corrections.length === 0 ? (
          <p className="text-muted-foreground">
            Nessuna correzione. Usa «Modifica» su una risposta del bot per insegnargli la versione
            giusta.
          </p>
        ) : (
          <ul className="space-y-2">
            {corrections.map((c) => (
              <li key={c.id} className="rounded-md border border-border p-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 space-y-1">
                    <p className="truncate font-medium text-foreground" title={c.trigger_message}>
                      «{c.trigger_message}»
                    </p>
                    <p className="text-muted-foreground line-through" title={c.original_response}>
                      {c.original_response}
                    </p>
                    <p className="text-emerald-600" title={c.corrected_response}>
                      → {c.corrected_response}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Elimina correzione"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(c.id)}
                  >
                    <Trash2 className="h-4 w-4 text-destructive" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {remove.isError ? (
          <p className="text-destructive">{apiErrorMessage(remove.error)}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
