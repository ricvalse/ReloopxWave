'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import {
  Button,
  ButtonSpinner,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  SkeletonForm,
  Textarea,
} from '@reloop/ui';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type BotConfig = components['schemas']['BotConfigSchema'];
type OverridesOut = components['schemas']['OverridesOut'];

type Field = {
  key: keyof BusinessForm;
  label: string;
  placeholder: string;
  textarea?: boolean;
  rows?: number;
};

type BusinessForm = {
  name: string;
  industry: string;
  description: string;
  offer: string;
  hours: string;
  location: string;
  pricing_notes: string;
  website: string;
};

const EMPTY: BusinessForm = {
  name: '',
  industry: '',
  description: '',
  offer: '',
  hours: '',
  location: '',
  pricing_notes: '',
  website: '',
};

const FIELDS: Field[] = [
  { key: 'name', label: 'Nome del brand', placeholder: 'Es: Fashion House Milano' },
  { key: 'industry', label: 'Settore', placeholder: 'Es: abbigliamento donna, beauty, arredamento' },
  {
    key: 'description',
    label: 'Descrizione',
    placeholder:
      'Es: Negozio di abbigliamento donna fondato nel 2018, specializzato in capi sartoriali made in Italy…',
    textarea: true,
    rows: 3,
  },
  {
    key: 'offer',
    label: 'Punto di forza (USP)',
    placeholder: 'Es: Tessuti 100% italiani, spedizione in 24h, reso gratuito entro 30 giorni',
    textarea: true,
    rows: 2,
  },
  { key: 'hours', label: 'Orari', placeholder: 'Es: Lun-Sab 9:00-19:30' },
  { key: 'location', label: 'Sede / copertura', placeholder: 'Es: Milano, spedizioni in tutta Italia' },
  {
    key: 'pricing_notes',
    label: 'Note sui prezzi',
    placeholder: 'Es: fascia media, da 49€; sconto 10% dal secondo capo',
    textarea: true,
    rows: 2,
  },
  { key: 'website', label: 'Sito web', placeholder: 'https://…' },
];

function asStr(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

export function BrandInfoPanel() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<BusinessForm>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const resolvedQuery = useQuery({
    queryKey: ['bot-config', 'resolved', merchantId],
    enabled: !!merchantId,
    staleTime: 60_000,
    queryFn: async (): Promise<BotConfig> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/resolved' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(apiErrorMessage(error));
      return data as BotConfig;
    },
  });

  const overridesQuery = useQuery({
    queryKey: ['bot-config', 'overrides', merchantId],
    enabled: !!merchantId,
    staleTime: 60_000,
    queryFn: async (): Promise<OverridesOut> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/overrides' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(apiErrorMessage(error));
      return data as OverridesOut;
    },
  });

  // Prefill from the resolved (effective) business profile.
  useEffect(() => {
    const biz = (resolvedQuery.data as { business?: Record<string, unknown> } | undefined)?.business;
    if (!biz) return;
    setForm({
      name: asStr(biz.name),
      industry: asStr(biz.industry),
      description: asStr(biz.description),
      offer: asStr(biz.offer),
      hours: asStr(biz.hours),
      location: asStr(biz.location),
      pricing_notes: asStr(biz.pricing_notes),
      website: asStr(biz.website),
    });
  }, [resolvedQuery.data]);

  const save = useMutation({
    mutationFn: async () => {
      if (!merchantId) throw new Error('Merchant context mancante');
      // Preserve every other override key; replace only `business`. Empty fields
      // are dropped so they read as Inherited rather than an empty override.
      const existing = (overridesQuery.data?.overrides ?? {}) as Record<string, unknown>;
      const business: Record<string, string> = {};
      for (const [k, v] of Object.entries(form)) {
        if (v.trim()) business[k] = v.trim();
      }
      const overrides = { ...existing, business };
      const api = getApiClient();
      const { error } = await api.PUT('/bot-config/{merchant_id}/overrides' as never, {
        params: { path: { merchant_id: merchantId } },
        body: { overrides },
      } as never);
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bot-config', 'resolved', merchantId] });
      void queryClient.invalidateQueries({ queryKey: ['bot-config', 'overrides', merchantId] });
      setError(null);
      setSaved(true);
    },
    onError: (e) => {
      setError(apiErrorMessage(e));
      setSaved(false);
    },
  });

  const set = (key: keyof BusinessForm, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  };

  if (resolvedQuery.isLoading || overridesQuery.isLoading) {
    return (
      <div className="p-6">
        <Card>
          <CardHeader>
            <CardTitle>Profilo</CardTitle>
          </CardHeader>
          <CardContent>
            <SkeletonForm fields={8} />
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6">
      <Card>
        <CardHeader>
          <CardTitle>Profilo</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {FIELDS.map((f) => (
            <div key={f.key} className="space-y-1.5">
              <Label htmlFor={`brand-${f.key}`}>{f.label}</Label>
              {f.textarea ? (
                <Textarea
                  id={`brand-${f.key}`}
                  rows={f.rows ?? 3}
                  placeholder={f.placeholder}
                  value={form[f.key]}
                  onChange={(e) => set(f.key, e.target.value)}
                />
              ) : (
                <Input
                  id={`brand-${f.key}`}
                  placeholder={f.placeholder}
                  value={form[f.key]}
                  onChange={(e) => set(f.key, e.target.value)}
                />
              )}
            </div>
          ))}

          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          <div className="flex items-center justify-end gap-3">
            {saved ? <span className="text-sm text-emerald-600">Salvato ✓</span> : null}
            <Button onClick={() => save.mutate()} disabled={save.isPending}>
              {save.isPending ? (
                <>
                  <ButtonSpinner />
                  Salvataggio…
                </>
              ) : (
                'Salva'
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
