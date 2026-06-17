'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import {
  Badge,
  Button,
  ButtonSpinner,
  Card,
  CardContent,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  SkeletonList,
  Switch,
  Textarea,
} from '@reloop/ui';
import { ArrowDown, ArrowUp, HelpCircle, Pencil, Plus, Trash2 } from 'lucide-react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type Faq = components['schemas']['FaqOut'];

const MAX = 50;

export function FaqPanel() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Faq | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [seq, setSeq] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['faq', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<Faq[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/catalog/{merchant_id}/faq', {
        params: { path: { merchant_id: merchantId! } },
      });
      if (error) throw new Error(apiErrorMessage(error));
      return (data as Faq[]) ?? [];
    },
  });

  const entries = query.data ?? [];

  const putEntry = async (e: Faq) => {
    const api = getApiClient();
    const { error } = await api.PUT('/catalog/{merchant_id}/faq/{faq_id}', {
      params: { path: { merchant_id: merchantId!, faq_id: e.id } },
      body: {
        question: e.question,
        answer: e.answer,
        category: e.category,
        sort_order: e.sort_order,
        is_active: e.is_active,
      },
    });
    if (error) throw new Error(apiErrorMessage(error));
  };

  const toggleActive = useMutation({
    mutationFn: (e: Faq) => putEntry({ ...e, is_active: !e.is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['faq', merchantId] }),
  });

  const del = useMutation({
    mutationFn: async (id: string) => {
      const api = getApiClient();
      const { error } = await api.DELETE('/catalog/{merchant_id}/faq/{faq_id}', {
        params: { path: { merchant_id: merchantId!, faq_id: id } },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['faq', merchantId] }),
    onSettled: () => setConfirmDelete(null),
  });

  // Move an entry up/down: renumber array positions and persist only the entries
  // whose stored sort_order no longer matches their new index.
  const reorder = useMutation({
    mutationFn: async (dir: { id: string; delta: -1 | 1 }) => {
      const idx = entries.findIndex((e) => e.id === dir.id);
      const target = idx + dir.delta;
      if (idx < 0 || target < 0 || target >= entries.length) return;
      const next = [...entries];
      const [moved] = next.splice(idx, 1);
      next.splice(target, 0, moved as Faq);
      const changed = next
        .map((e, i) => ({ e, i }))
        .filter(({ e, i }) => e.sort_order !== i);
      await Promise.all(changed.map(({ e, i }) => putEntry({ ...e, sort_order: i })));
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['faq', merchantId] }),
  });

  const openNew = () => {
    setEditing(null);
    setSeq((s) => s + 1);
    setDialogOpen(true);
  };
  const openEdit = (e: Faq) => {
    setEditing(e);
    setSeq((s) => s + 1);
    setDialogOpen(true);
  };

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {entries.length}/{MAX} FAQ
        </p>
        <Button onClick={openNew} disabled={entries.length >= MAX}>
          <Plus className="h-4 w-4" />
          Aggiungi FAQ
        </Button>
      </div>
      {entries.length >= MAX ? (
        <p className="text-xs text-muted-foreground">Hai raggiunto il massimo di {MAX} FAQ.</p>
      ) : null}

      {query.isLoading ? (
        <Card>
          <CardContent className="py-2">
            <SkeletonList rows={5} />
          </CardContent>
        </Card>
      ) : entries.length === 0 ? (
        <Card>
          <CardContent className="py-4">
            <EmptyState
              icon={HelpCircle}
              title="Nessuna FAQ"
              description="Aggiungi le domande più frequenti dei clienti con la risposta giusta."
              action={
                <Button onClick={openNew}>
                  <Plus className="h-4 w-4" />
                  Aggiungi FAQ
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {entries.map((e, i) => (
            <Card key={e.id}>
              <CardContent className="flex items-start gap-3 p-4">
                <div className="flex flex-col gap-1 pt-0.5">
                  <button
                    type="button"
                    aria-label="Sposta su"
                    disabled={i === 0 || reorder.isPending}
                    className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                    onClick={() => reorder.mutate({ id: e.id, delta: -1 })}
                  >
                    <ArrowUp className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    aria-label="Sposta giù"
                    disabled={i === entries.length - 1 || reorder.isPending}
                    className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                    onClick={() => reorder.mutate({ id: e.id, delta: 1 })}
                  >
                    <ArrowDown className="h-4 w-4" />
                  </button>
                </div>
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{e.question}</p>
                    {e.category ? <Badge variant="secondary">{e.category}</Badge> : null}
                    {!e.is_active ? <Badge variant="outline">Disattiva</Badge> : null}
                  </div>
                  <p className="text-sm text-muted-foreground">{e.answer}</p>
                  <div className="flex items-center gap-3 pt-1">
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                      onClick={() => openEdit(e)}
                    >
                      <Pencil className="h-3.5 w-3.5" /> Modifica
                    </button>
                    {confirmDelete === e.id ? (
                      <span className="inline-flex items-center gap-2 text-xs">
                        <button
                          type="button"
                          className="text-destructive hover:underline"
                          onClick={() => del.mutate(e.id)}
                          disabled={del.isPending}
                        >
                          Conferma
                        </button>
                        <button
                          type="button"
                          className="text-muted-foreground hover:underline"
                          onClick={() => setConfirmDelete(null)}
                        >
                          Annulla
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 text-xs text-destructive/80 hover:text-destructive"
                        onClick={() => setConfirmDelete(e.id)}
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Elimina
                      </button>
                    )}
                  </div>
                </div>
                <Switch
                  checked={e.is_active}
                  onCheckedChange={() => toggleActive.mutate(e)}
                  aria-label="Attiva/disattiva FAQ"
                />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <FaqFormDialog
        key={seq}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        entry={editing}
        merchantId={merchantId}
        nextSortOrder={entries.length}
      />
    </div>
  );
}

function FaqFormDialog({
  open,
  onOpenChange,
  entry,
  merchantId,
  nextSortOrder,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  entry: Faq | null;
  merchantId: string | null;
  nextSortOrder: number;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState(entry?.question ?? '');
  const [answer, setAnswer] = useState(entry?.answer ?? '');
  const [category, setCategory] = useState(entry?.category ?? '');

  const save = useMutation({
    mutationFn: async () => {
      if (!merchantId) throw new Error('Merchant context mancante');
      const body = {
        question: question.trim(),
        answer: answer.trim(),
        category: category.trim() || null,
        sort_order: entry?.sort_order ?? nextSortOrder,
        is_active: entry?.is_active ?? true,
      };
      const api = getApiClient();
      if (entry) {
        const { error } = await api.PUT('/catalog/{merchant_id}/faq/{faq_id}', {
          params: { path: { merchant_id: merchantId, faq_id: entry.id } },
          body,
        });
        if (error) throw new Error(apiErrorMessage(error));
      } else {
        const { error } = await api.POST('/catalog/{merchant_id}/faq', {
          params: { path: { merchant_id: merchantId } },
          body,
        });
        if (error) throw new Error(apiErrorMessage(error));
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['faq', merchantId] });
      onOpenChange(false);
    },
    onError: (e) => setError(apiErrorMessage(e)),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{entry ? 'Modifica FAQ' : 'Nuova FAQ'}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="faq-q">Domanda</Label>
            <Input
              id="faq-q"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Es: Quanto costa la spedizione?"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="faq-a">Risposta</Label>
            <Textarea
              id="faq-a"
              rows={3}
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder="Es: Gratuita sopra i 49€, altrimenti 5,90€."
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="faq-cat">Categoria (opzionale)</Label>
            <Input
              id="faq-cat"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="Es: Spedizioni"
            />
          </div>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={save.isPending}>
            Annulla
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={!question.trim() || !answer.trim() || save.isPending}
          >
            {save.isPending ? (
              <>
                <ButtonSpinner />
                Salvataggio…
              </>
            ) : (
              'Salva'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
