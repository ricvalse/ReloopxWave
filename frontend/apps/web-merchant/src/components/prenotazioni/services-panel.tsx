'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  SkeletonCard,
  Switch,
  Textarea,
} from '@reloop/ui';
import { Clock, Euro, Pencil, Plus, Scissors, Trash2 } from 'lucide-react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type Service = {
  id: string;
  name: string;
  handle: string;
  description: string | null;
  duration_min: number;
  buffer_min: number;
  price: number | null;
  currency: string;
  ghl_calendar_id: string | null;
  sort_order: number;
  is_active: boolean;
};

type ServiceForm = {
  name: string;
  description: string;
  duration_min: string;
  buffer_min: string;
  price: string;
  currency: string;
  ghl_calendar_id: string;
  sort_order: string;
  is_active: boolean;
};

const empty: ServiceForm = {
  name: '',
  description: '',
  duration_min: '30',
  buffer_min: '0',
  price: '',
  currency: 'EUR',
  ghl_calendar_id: '',
  sort_order: '0',
  is_active: true,
};

function fromService(s: Service): ServiceForm {
  return {
    name: s.name,
    description: s.description ?? '',
    duration_min: String(s.duration_min),
    buffer_min: String(s.buffer_min),
    price: s.price != null ? String(s.price) : '',
    currency: s.currency,
    ghl_calendar_id: s.ghl_calendar_id ?? '',
    sort_order: String(s.sort_order),
    is_active: s.is_active,
  };
}

export function ServicesPanel() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Service | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [form, setForm] = useState<ServiceForm>(empty);
  const [formError, setFormError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['services', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<Service[]> => {
      if (!merchantId) return [];
      const api = getApiClient();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { data, error } = await (api.GET as any)('/services/{merchant_id}', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(apiErrorMessage(error));
      return (data as Service[]) ?? [];
    },
  });

  const save = useMutation({
    mutationFn: async (f: ServiceForm) => {
      if (!merchantId) throw new Error('Merchant context mancante');
      const api = getApiClient();
      const body = {
        name: f.name.trim(),
        description: f.description.trim() || null,
        duration_min: parseInt(f.duration_min, 10),
        buffer_min: parseInt(f.buffer_min, 10),
        price: f.price ? parseFloat(f.price) : null,
        currency: f.currency || 'EUR',
        ghl_calendar_id: f.ghl_calendar_id.trim() || null,
        sort_order: parseInt(f.sort_order, 10) || 0,
        is_active: f.is_active,
      };
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const apiAny = api as any;
      if (editing) {
        const { error } = await apiAny.PUT('/services/{merchant_id}/{service_id}', {
          params: { path: { merchant_id: merchantId, service_id: editing.id } },
          body,
        });
        if (error) throw new Error(apiErrorMessage(error));
      } else {
        const { error } = await apiAny.POST('/services/{merchant_id}', {
          params: { path: { merchant_id: merchantId } },
          body,
        });
        if (error) throw new Error(apiErrorMessage(error));
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['services', merchantId] });
      setDialogOpen(false);
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const del = useMutation({
    mutationFn: async (id: string) => {
      if (!merchantId) throw new Error('Merchant context mancante');
      const api = getApiClient();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { error } = await (api as any).DELETE('/services/{merchant_id}/{service_id}', {
        params: { path: { merchant_id: merchantId, service_id: id } },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['services', merchantId] }),
    onSettled: () => setConfirmDelete(null),
  });

  const openNew = () => {
    setEditing(null);
    setForm(empty);
    setFormError(null);
    setDialogOpen(true);
  };
  const openEdit = (s: Service) => {
    setEditing(s);
    setForm(fromService(s));
    setFormError(null);
    setDialogOpen(true);
  };

  const services = query.data ?? [];

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Servizi prenotabili</h2>
          <p className="text-sm text-muted-foreground">
            Ogni servizio ha una durata propria usata dal bot per calcolare lo slot.
          </p>
        </div>
        <Button onClick={openNew} size="sm">
          <Plus className="mr-2 h-4 w-4" />
          Aggiungi
        </Button>
      </div>

      {query.isPending && <SkeletonCard />}

      {!query.isPending && services.length === 0 && (
        <EmptyState
          icon={Scissors}
          title="Nessun servizio configurato"
          description="Aggiungi il primo servizio che il bot può proporre durante la conversazione."
          action={<Button size="sm" onClick={openNew}><Plus className="mr-2 h-4 w-4" />Aggiungi servizio</Button>}
        />
      )}

      {services.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <div className="divide-y">
              {services.map((svc) => (
                <div key={svc.id} className="flex items-center gap-4 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium truncate">{svc.name}</span>
                      {!svc.is_active && (
                        <Badge variant="secondary" className="text-xs shrink-0">
                          Disattivo
                        </Badge>
                      )}
                    </div>
                    {svc.description && (
                      <p className="text-sm text-muted-foreground truncate">{svc.description}</p>
                    )}
                    <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {svc.duration_min} min
                        {svc.buffer_min > 0 && ` + ${svc.buffer_min} min buffer`}
                      </span>
                      {svc.price != null && (
                        <span className="flex items-center gap-1">
                          <Euro className="h-3 w-3" />
                          {Number(svc.price).toFixed(2)} {svc.currency}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <Button variant="ghost" size="icon" onClick={() => openEdit(svc)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:text-destructive"
                      onClick={() => setConfirmDelete(svc.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Dialog creazione / modifica */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing ? 'Modifica servizio' : 'Nuovo servizio'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label>Nome *</Label>
              <Input
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="es. Taglio capelli"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Descrizione</Label>
              <Textarea
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="Breve descrizione mostrata al cliente"
                rows={2}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>Durata (minuti) *</Label>
                <Input
                  type="number"
                  min={5}
                  max={480}
                  value={form.duration_min}
                  onChange={(e) => setForm((f) => ({ ...f, duration_min: e.target.value }))}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Buffer post-appuntamento (min)</Label>
                <Input
                  type="number"
                  min={0}
                  max={120}
                  value={form.buffer_min}
                  onChange={(e) => setForm((f) => ({ ...f, buffer_min: e.target.value }))}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>Prezzo (opzionale)</Label>
                <Input
                  type="number"
                  min={0}
                  step={0.01}
                  value={form.price}
                  onChange={(e) => setForm((f) => ({ ...f, price: e.target.value }))}
                  placeholder="es. 25.00"
                />
              </div>
              <div className="space-y-1.5">
                <Label>Valuta</Label>
                <Input
                  value={form.currency}
                  onChange={(e) => setForm((f) => ({ ...f, currency: e.target.value }))}
                  maxLength={3}
                  placeholder="EUR"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>ID Calendario GHL (opzionale)</Label>
              <Input
                value={form.ghl_calendar_id}
                onChange={(e) => setForm((f) => ({ ...f, ghl_calendar_id: e.target.value }))}
                placeholder="Sovrascrive il calendario di default"
                className="font-mono text-sm"
              />
            </div>
            <div className="flex items-center justify-between">
              <Label>Servizio attivo</Label>
              <Switch
                checked={form.is_active}
                onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v }))}
              />
            </div>
            {formError && <p className="text-sm text-destructive">{formError}</p>}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Annulla
            </Button>
            <Button
              onClick={() => save.mutate(form)}
              disabled={!form.name.trim() || save.isPending}
            >
              {save.isPending && <ButtonSpinner />}
              {editing ? 'Salva modifiche' : 'Crea servizio'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Dialog conferma eliminazione */}
      <Dialog open={!!confirmDelete} onOpenChange={() => setConfirmDelete(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Elimina servizio</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Questa azione è irreversibile. Gli appuntamenti già prenotati per questo servizio non
            verranno eliminati.
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
              Elimina
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
