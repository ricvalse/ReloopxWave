'use client';

import type { components } from '@reloop/api-client';
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@reloop/ui';
import { useQuery } from '@tanstack/react-query';
import { Check, ChevronDown, Store } from 'lucide-react';
import { useMemo } from 'react';
import { getApiClient } from '@/lib/api';

type Merchant = components['schemas']['MerchantOut'];

interface MerchantPickerProps {
  value: string | null;
  onChange: (merchantId: string | null) => void;
}

export function MerchantPicker({ value, onChange }: MerchantPickerProps) {
  const query = useQuery({
    queryKey: ['merchants', 'list'],
    queryFn: async (): Promise<Merchant[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/merchants/' as never, {} as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant[];
    },
    staleTime: 60_000,
  });

  const merchants = useMemo(() => query.data ?? [], [query.data]);
  const selected = useMemo(
    () => (value ? merchants.find((m) => m.id === value) ?? null : null),
    [merchants, value],
  );

  const label = selected?.name ?? (value && query.isLoading ? 'Caricamento…' : 'Tutti i merchant');

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-8 gap-2 text-xs"
          disabled={query.isLoading && merchants.length === 0}
        >
          <Store className="h-3.5 w-3.5" />
          <span className="max-w-[180px] truncate">{label}</span>
          <ChevronDown className="h-3.5 w-3.5 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuLabel>Filtra per merchant</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => onChange(null)} className="justify-between">
          <span>Tutti i merchant</span>
          {value === null && <Check className="h-4 w-4" />}
        </DropdownMenuItem>
        {query.isError && (
          <DropdownMenuItem disabled className="text-destructive">
            Errore nel caricamento merchant
          </DropdownMenuItem>
        )}
        {merchants.length > 0 && <DropdownMenuSeparator />}
        {merchants.map((m) => (
          <DropdownMenuItem
            key={m.id}
            onSelect={() => onChange(m.id)}
            className="justify-between"
          >
            <span className="truncate">{m.name}</span>
            {value === m.id && <Check className="h-4 w-4 shrink-0" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
