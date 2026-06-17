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
import { Plus, Trash2 } from 'lucide-react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type Policy = components['schemas']['PolicyOut'];
type CustomPolicy = { title: string; body: string };

type Field = { key: keyof PolicyForm; label: string; placeholder: string };

type PolicyForm = {
  shipping_info: string;
  return_policy: string;
  payment_methods: string;
  exchange_policy: string;
  warranty_info: string;
  contact_info: string;
};

const EMPTY: PolicyForm = {
  shipping_info: '',
  return_policy: '',
  payment_methods: '',
  exchange_policy: '',
  warranty_info: '',
  contact_info: '',
};

const FIELDS: Field[] = [
  {
    key: 'shipping_info',
    label: 'Spedizioni',
    placeholder: 'Es: spedizione gratuita sopra 49€, consegna in 24/48h con corriere espresso.',
  },
  {
    key: 'return_policy',
    label: 'Resi e rimborsi',
    placeholder: 'Es: reso gratuito entro 30 giorni, capi integri con cartellino.',
  },
  {
    key: 'payment_methods',
    label: 'Pagamenti',
    placeholder: 'Es: carta, PayPal, Apple Pay, bonifico, pagamento alla consegna.',
  },
  { key: 'exchange_policy', label: 'Cambi', placeholder: 'Es: cambio taglia/colore gratuito.' },
  {
    key: 'warranty_info',
    label: 'Garanzia',
    placeholder: 'Es: garanzia legale di 24 mesi sui difetti di fabbricazione.',
  },
  {
    key: 'contact_info',
    label: 'Contatti',
    placeholder: 'Es: WhatsApp e supporto@brand.it, Lun-Sab 9:00-19:30.',
  },
];

function asStr(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

export function StorePoliciesPanel() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<PolicyForm>(EMPTY);
  const [custom, setCustom] = useState<CustomPolicy[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const query = useQuery({
    queryKey: ['store-policies', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<Policy> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/catalog/{merchant_id}/policies' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(apiErrorMessage(error));
      return data as Policy;
    },
  });

  useEffect(() => {
    const d = query.data as Policy | undefined;
    if (!d) return;
    setForm({
      shipping_info: asStr(d.shipping_info),
      return_policy: asStr(d.return_policy),
      payment_methods: asStr(d.payment_methods),
      exchange_policy: asStr(d.exchange_policy),
      warranty_info: asStr(d.warranty_info),
      contact_info: asStr(d.contact_info),
    });
    setCustom(
      (d.custom_policies ?? []).map((c) => ({ title: asStr(c.title), body: asStr(c.body) })),
    );
  }, [query.data]);

  const save = useMutation({
    mutationFn: async () => {
      if (!merchantId) throw new Error('Merchant context mancante');
      const body: Record<string, unknown> = {
        custom_policies: custom.filter((c) => c.title.trim() && c.body.trim()),
      };
      for (const [k, v] of Object.entries(form)) body[k] = v.trim() || null;
      const api = getApiClient();
      const { error } = await api.PUT('/catalog/{merchant_id}/policies' as never, {
        params: { path: { merchant_id: merchantId } },
        body,
      } as never);
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['store-policies', merchantId] });
      setError(null);
      setSaved(true);
    },
    onError: (e) => {
      setError(apiErrorMessage(e));
      setSaved(false);
    },
  });

  const set = (key: keyof PolicyForm, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  };
  const setCustomAt = (i: number, patch: Partial<CustomPolicy>) => {
    setCustom((prev) => prev.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
    setSaved(false);
  };

  if (query.isLoading) {
    return (
      <div className="p-6">
        <Card>
          <CardHeader>
            <CardTitle>Policy</CardTitle>
          </CardHeader>
          <CardContent>
            <SkeletonForm fields={6} />
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-6">
      <Card>
        <CardHeader>
          <CardTitle>Policy standard</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {FIELDS.map((f) => (
            <div key={f.key} className="space-y-1.5">
              <Label htmlFor={`pol-${f.key}`}>{f.label}</Label>
              <Textarea
                id={`pol-${f.key}`}
                rows={2}
                placeholder={f.placeholder}
                value={form[f.key]}
                onChange={(e) => set(f.key, e.target.value)}
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Policy personalizzate</CardTitle>
          <p className="text-sm text-muted-foreground">
            Aggiungi regole extra (es. confezioni regalo, programma fedeltà).
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          {custom.map((c, i) => (
            <div key={i} className="grid gap-2 sm:grid-cols-[1fr_2fr_auto] sm:items-start">
              <Input
                placeholder="Titolo"
                value={c.title}
                onChange={(e) => setCustomAt(i, { title: e.target.value })}
              />
              <Input
                placeholder="Testo"
                value={c.body}
                onChange={(e) => setCustomAt(i, { body: e.target.value })}
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label="Rimuovi"
                onClick={() => setCustom((prev) => prev.filter((_, idx) => idx !== i))}
              >
                <Trash2 className="h-4 w-4 text-destructive" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCustom((prev) => [...prev, { title: '', body: '' }])}
          >
            <Plus className="h-4 w-4" />
            Aggiungi policy
          </Button>
        </CardContent>
      </Card>

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
    </div>
  );
}
