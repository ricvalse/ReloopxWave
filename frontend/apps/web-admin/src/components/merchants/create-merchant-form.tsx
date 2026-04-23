'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

type MerchantIn = components['schemas']['MerchantIn'];
type Merchant = components['schemas']['MerchantOut'];

export function CreateMerchantForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [slug, setSlug] = useState('');
  const [name, setName] = useState('');
  const [timezone, setTimezone] = useState('Europe/Rome');
  const [locale, setLocale] = useState('it');

  const create = useMutation({
    mutationFn: async (payload: MerchantIn): Promise<Merchant> => {
      const api = getApiClient();
      const { data, error } = await api.POST('/merchants/' as never, {
        body: payload,
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['merchants', 'list'] });
      onClose();
    },
  });

  return (
    <div className="p-6">
      <Card>
        <CardContent className="space-y-4 p-6">
          <h3 className="text-lg font-semibold">Nuovo merchant</h3>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate({ slug, name, timezone, locale });
            }}
          >
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="slug">
                  Slug
                </label>
                <input
                  id="slug"
                  required
                  pattern="^[a-z0-9][a-z0-9-]*$"
                  placeholder="es. pizzeria-roma"
                  value={slug}
                  onChange={(e) => setSlug(e.target.value.toLowerCase())}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="name">
                  Nome
                </label>
                <input
                  id="name"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="timezone">
                  Timezone
                </label>
                <input
                  id="timezone"
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="locale">
                  Locale
                </label>
                <input
                  id="locale"
                  value={locale}
                  maxLength={8}
                  onChange={(e) => setLocale(e.target.value.toLowerCase())}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>
            </div>
            {create.error ? (
              <p className="text-sm text-destructive">
                {create.error instanceof Error ? create.error.message : 'Errore'}
              </p>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={onClose} disabled={create.isPending}>
                Annulla
              </Button>
              <Button type="submit" disabled={create.isPending || !slug || !name}>
                {create.isPending ? 'Creazione…' : 'Crea merchant'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
