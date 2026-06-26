'use client';

import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@reloop/ui';
import { toast } from '@reloop/ui';
import { getApiClient, apiFetch } from '@/lib/api';

type Merchant = components['schemas']['MerchantOut'];
type Template = components['schemas']['TemplateOut'];

interface BulkApplyResult {
  applied: string[];
  skipped: string[];
  errors: { id: string; reason: string }[];
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** Pre-select a template (from templates panel). */
  preselectedTemplateId?: string;
  /** Pre-select merchants (from merchant list bulk action). */
  preselectedMerchantIds?: string[];
}

export function BulkApplyDialog({
  open,
  onClose,
  preselectedTemplateId,
  preselectedMerchantIds,
}: Props) {
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(
    preselectedTemplateId ?? null,
  );
  const [selectedMerchantIds, setSelectedMerchantIds] = useState<Set<string>>(
    new Set(preselectedMerchantIds ?? []),
  );

  const templates = useQuery({
    queryKey: ['templates', 'list'],
    queryFn: async (): Promise<Template[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/templates');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Template[];
    },
    enabled: open,
  });

  const merchants = useQuery({
    queryKey: ['merchants', 'list'],
    queryFn: async (): Promise<Merchant[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/merchants/');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant[];
    },
    enabled: open,
  });

  const apply = useMutation({
    mutationFn: async () => {
      if (!selectedTemplateId) throw new Error('Seleziona un profilo');
      if (selectedMerchantIds.size === 0) throw new Error('Seleziona almeno un merchant');
      return apiFetch<BulkApplyResult>(
        `/bot-config/templates/${selectedTemplateId}/bulk-apply`,
        {
          method: 'POST',
          body: JSON.stringify({ merchant_ids: [...selectedMerchantIds] }),
        },
      );
    },
    onSuccess: (res) => {
      const n = res.applied.length;
      if (res.errors.length > 0) {
        toast.error(`Applicato a ${n} merchant — ${res.errors.length} errori`);
      } else {
        toast.success(`Profilo collegato a ${n} merchant`);
      }
      onClose();
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : 'Errore');
    },
  });

  function toggleMerchant(id: string) {
    setSelectedMerchantIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const selectedTemplate = templates.data?.find((t) => t.id === selectedTemplateId);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[80vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Collega profilo ai merchant</DialogTitle>
        </DialogHeader>

        {/* Template picker */}
        <div className="space-y-2">
          <p className="text-sm font-medium">1. Scegli profilo</p>
          {templates.isLoading ? (
            <p className="text-sm text-muted-foreground">Caricamento...</p>
          ) : (templates.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">Nessun profilo disponibile.</p>
          ) : (
            <div className="grid gap-2">
              {(templates.data ?? []).map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setSelectedTemplateId(t.id)}
                  className={`rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                    selectedTemplateId === t.id
                      ? 'border-primary bg-primary/5 text-primary'
                      : 'border-border bg-background hover:bg-muted/50'
                  }`}
                >
                  <span className="font-medium">{t.name}</span>
                  {t.is_default ? (
                    <span className="ml-2 rounded-full bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
                      Default
                    </span>
                  ) : null}
                  {t.description ? (
                    <span className="ml-2 text-muted-foreground">{t.description}</span>
                  ) : null}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Merchant multi-select */}
        <div className="space-y-2">
          <p className="text-sm font-medium">
            2. Seleziona merchant{' '}
            {selectedMerchantIds.size > 0 ? (
              <span className="text-muted-foreground">({selectedMerchantIds.size} selezionati)</span>
            ) : null}
          </p>
          {merchants.isLoading ? (
            <p className="text-sm text-muted-foreground">Caricamento...</p>
          ) : (merchants.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">Nessun merchant disponibile.</p>
          ) : (
            <div className="max-h-48 overflow-y-auto rounded-md border">
              {(merchants.data ?? []).map((m) => {
                const checked = selectedMerchantIds.has(m.id);
                return (
                  <label
                    key={m.id}
                    className={`flex cursor-pointer items-center gap-3 border-b px-3 py-2 text-sm last:border-b-0 hover:bg-muted/50 ${
                      checked ? 'bg-primary/5' : ''
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleMerchant(m.id)}
                      className="h-4 w-4 rounded border-border"
                    />
                    <span className="font-medium">{m.name}</span>
                    <span className="text-muted-foreground">{m.slug}</span>
                  </label>
                );
              })}
            </div>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground"
              onClick={() =>
                setSelectedMerchantIds(new Set((merchants.data ?? []).map((m) => m.id)))
              }
            >
              Seleziona tutti
            </button>
            <span className="text-xs text-muted-foreground">·</span>
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground"
              onClick={() => setSelectedMerchantIds(new Set())}
            >
              Deseleziona tutti
            </button>
          </div>
        </div>

        {/* Preview */}
        {selectedTemplate && selectedMerchantIds.size > 0 ? (
          <div className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-sm">
            Collegherà il profilo <strong>{selectedTemplate.name}</strong> a{' '}
            <strong>{selectedMerchantIds.size}</strong> merchant. Gli override esistenti
            vengono mantenuti e avranno priorità sul profilo.
          </div>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={apply.isPending}>
            Annulla
          </Button>
          <Button
            onClick={() => apply.mutate()}
            disabled={
              apply.isPending || !selectedTemplateId || selectedMerchantIds.size === 0
            }
          >
            {apply.isPending
              ? 'Applicazione...'
              : `Collega ${selectedMerchantIds.size > 0 ? selectedMerchantIds.size : ''} merchant al profilo`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
