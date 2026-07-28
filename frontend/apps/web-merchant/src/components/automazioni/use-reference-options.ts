'use client';

import { useQuery } from '@tanstack/react-query';
import { getApiClient } from '@/lib/api';

export type ReferenceOption = { value: string; label: string };

/** Statistiche personalizzate del merchant, per le tendine dei nodi.
 *
 * È il punto per cui un esito è una riga e non una stringa: il nodo lo **sceglie**
 * dallo stesso elenco che legge il metric-builder, quindi emettitore e lettore non
 * possono divergere. Un refuso non è nemmeno esprimibile.
 */
export function useOutcomeOptions() {
  return useQuery({
    queryKey: ['outcome-definitions', 'options'],
    staleTime: 60_000,
    queryFn: async (): Promise<ReferenceOption[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/statistics/outcomes', {
        params: { query: { enabled_only: true } },
      });
      if (error) throw new Error(JSON.stringify(error));
      return (data ?? []).map((o) => ({ value: o.id, label: o.label }));
    },
  });
}

/** Profili di conversazione, per il nodo «Carica profilo» e per il cancello
 *  «Profilo attivo». */
export function useProfileOptions() {
  return useQuery({
    queryKey: ['conversation-profiles', 'options'],
    staleTime: 60_000,
    queryFn: async (): Promise<ReferenceOption[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/statistics/profiles');
      if (error) throw new Error(JSON.stringify(error));
      return (data ?? [])
        .filter((p) => p.enabled)
        .map((p) => ({ value: p.id, label: p.is_default ? `${p.name} (default)` : p.name }));
    },
  });
}
