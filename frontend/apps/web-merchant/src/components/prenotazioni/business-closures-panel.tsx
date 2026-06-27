'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
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
  SkeletonCard,
} from '@reloop/ui';
import { CalendarOff, Plus, Trash2 } from 'lucide-react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type Closure = {
  id: string;
  closed_on: string;
  label: string | null;
};

export function BusinessClosuresPanel() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [closedOn, setClosedOn] = useState('');
  const [label, setLabel] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['business-closures', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<Closure[]> => {
      if (!merchantId) return [];
      const api = getApiClient();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { data, error } = await (api as any).GET('/services/{merchant_id}/closures', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(apiErrorMessage(error));
      return (data as Closure[]) ?? [];
    },
  });

  const add = useMutation({
    mutationFn: async () => {
      if (!merchantId || !closedOn) throw new Error('Data mancante');
      const api = getApiClient();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { error } = await (api as any).POST('/services/{merchant_id}/closures', {
        params: { path: { merchant_id: merchantId } },
        body: { closed_on: closedOn, label: label.trim() || null },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['business-closures', merchantId] });
      setDialogOpen(false);
      setClosedOn('');
      setLabel('');
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const del = useMutation({
    mutationFn: async (id: string) => {
      if (!merchantId) throw new Error('Merchant context mancante');
      const api = getApiClient();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { error } = await (api as any).DELETE('/services/{merchant_id}/closures/{closure_id}', {
        params: { path: { merchant_id: merchantId, closure_id: id } },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['business-closures', merchantId] }),
    onSettled: () => setConfirmDelete(null),
  });

  const openDialog = () => {
    setClosedOn('');
    setLabel('');
    setFormError(null);
    setDialogOpen(true);
  };

  const closures = query.data ?? [];

  const formatDate = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString('it-IT', {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Chiusure eccezionali</h2>
          <p className="text-sm text-muted-foreground">
            Date in cui il merchant è chiuso (festività, ferie, …). Il bot non proporrà slot in
            queste giornate.
          </p>
        </div>
        <Button onClick={openDialog} size="sm">
          <Plus className="mr-2 h-4 w-4" />
          Aggiungi
        </Button>
      </div>

      {query.isPending && <SkeletonCard />}

      {!query.isPending && closures.length === 0 && (
        <EmptyState
          icon={CalendarOff}
          title="Nessuna chiusura programmata"
          description="Aggiungi le date in cui il merchant è chiuso."
          action={<Button size="sm" onClick={openDialog}><Plus className="mr-2 h-4 w-4" />Aggiungi chiusura</Button>}
        />
      )}

      {closures.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <div className="divide-y">
              {closures.map((c) => (
                <div key={c.id} className="flex items-center gap-4 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium capitalize">{formatDate(c.closed_on)}</p>
                    {c.label && (
                      <p className="text-sm text-muted-foreground">{c.label}</p>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="text-destructive hover:text-destructive shrink-0"
                    onClick={() => setConfirmDelete(c.id)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Aggiungi chiusura</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label>Data *</Label>
              <Input
                type="date"
                value={closedOn}
                onChange={(e) => setClosedOn(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Etichetta (opzionale)</Label>
              <Input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="es. Natale, Ferie agosto"
              />
            </div>
            {formError && <p className="text-sm text-destructive">{formError}</p>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Annulla
            </Button>
            <Button onClick={() => add.mutate()} disabled={!closedOn || add.isPending}>
              {add.isPending && <ButtonSpinner />}
              Aggiungi
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!confirmDelete} onOpenChange={() => setConfirmDelete(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Rimuovi chiusura</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            La data verrà rimossa dalle chiusure programmate.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(null)}>
              Annulla
            </Button>
            <Button
              variant="destructive"
              onClick={() => confirmDelete && del.mutate(confirmDelete)}
              disabled={del.isPending}
            >
              {del.isPending && <ButtonSpinner />}
              Rimuovi
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
