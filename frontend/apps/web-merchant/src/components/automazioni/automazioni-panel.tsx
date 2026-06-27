'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
} from '@reloop/ui';
import { X } from 'lucide-react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';
import { AutomationEditor } from './automation-editor';

type Automation = components['schemas']['AutomationOut'];

type ConfigOverrides = {
  no_answer?: { first_reminder_min?: number; second_reminder_min?: number; max_followups?: number };
  reactivation?: { dormant_days?: number; interval_days?: number; max_attempts?: number };
  booking?: { reminder_schedule?: number[] };
};

const TRIGGER_LABEL: Record<string, string> = {
  message_received: 'Messaggio ricevuto',
  no_answer: 'Nessuna risposta',
  booking_created: 'Prenotazione creata',
  booking_failed: 'Prenotazione fallita',
  lead_dormant: 'Lead dormiente',
  // system_key labels
  reactivation: 'Riattivazione dormienti',
  booking_reminder: 'Promemoria appuntamento',
  first_contact: 'Primo contatto',
};

function NoAnswerSettings() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();

  const configQuery = useQuery({
    queryKey: ['bot-config', 'overrides', merchantId],
    enabled: !!merchantId,
    queryFn: async () => {
      if (!merchantId) return null;
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/overrides', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(apiErrorMessage(error));
      return data;
    },
  });

  const overrides = (configQuery.data?.overrides as ConfigOverrides)?.no_answer ?? {};
  const firstMin: number = overrides.first_reminder_min ?? 60;
  const secondMin: number = overrides.second_reminder_min ?? 1440;
  const maxFollowups: number = overrides.max_followups ?? 2;

  const save = useMutation({
    mutationFn: async (values: { first_reminder_min: number; second_reminder_min: number; max_followups: number }) => {
      if (!merchantId) throw new Error('Contesto merchant mancante');
      const api = getApiClient();
      const { error } = await api.PUT('/bot-config/{merchant_id}/overrides', {
        params: { path: { merchant_id: merchantId } },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        body: { overrides: { no_answer: values } } as any,
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bot-config', 'overrides', merchantId] });
    },
  });

  const [f, setF] = useState('');
  const [s, setS] = useState('');
  const [m, setM] = useState('');

  useEffect(() => {
    setF(String(firstMin));
    setS(String(secondMin));
    setM(String(maxFollowups));
  }, [firstMin, secondMin, maxFollowups]);

  const handleSave = () => {
    const fv = parseInt(f, 10);
    const sv = parseInt(s, 10);
    const mv = parseInt(m, 10);
    if (isNaN(fv) || fv < 30 || fv > 480) return;
    if (isNaN(sv) || sv < 720 || sv > 2880) return;
    if (isNaN(mv) || mv < 1 || mv > 4) return;
    save.mutate({ first_reminder_min: fv, second_reminder_min: sv, max_followups: mv });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Nessuna risposta</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">
          Configura i follow-up automatici quando il lead non risponde.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {configQuery.isLoading ? (
          <p className="text-sm text-muted-foreground">Caricamento…</p>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-1">
              <Label className="text-xs">1° follow-up (min)</Label>
              <Input
                type="number"
                min={30}
                max={480}
                value={f}
                onChange={(e) => setF(e.target.value)}
                disabled={save.isPending}
                className="h-8 text-sm"
              />
              <p className="text-xs text-muted-foreground">30–480 min</p>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">2° follow-up (min)</Label>
              <Input
                type="number"
                min={720}
                max={2880}
                value={s}
                onChange={(e) => setS(e.target.value)}
                disabled={save.isPending}
                className="h-8 text-sm"
              />
              <p className="text-xs text-muted-foreground">720–2880 min</p>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Max follow-up</Label>
              <Input
                type="number"
                min={1}
                max={4}
                value={m}
                onChange={(e) => setM(e.target.value)}
                disabled={save.isPending}
                className="h-8 text-sm"
              />
              <p className="text-xs text-muted-foreground">1–4</p>
            </div>
          </div>
        )}
        <Button size="sm" onClick={handleSave} disabled={save.isPending || configQuery.isLoading}>
          {save.isPending ? 'Salvataggio…' : 'Salva'}
        </Button>
        {save.isSuccess ? (
          <p className="text-xs text-green-600">Salvato.</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ReactivationSettings() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();

  const configQuery = useQuery({
    queryKey: ['bot-config', 'overrides', merchantId],
    enabled: !!merchantId,
    queryFn: async () => {
      if (!merchantId) return null;
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/overrides', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(apiErrorMessage(error));
      return data;
    },
  });

  const overrides = (configQuery.data?.overrides as ConfigOverrides)?.reactivation ?? {};
  const dormantDays: number = overrides.dormant_days ?? 30;
  const intervalDays: number = overrides.interval_days ?? 7;
  const maxAttempts: number = overrides.max_attempts ?? 3;

  const save = useMutation({
    mutationFn: async (values: { dormant_days: number; interval_days: number; max_attempts: number }) => {
      if (!merchantId) throw new Error('Contesto merchant mancante');
      const api = getApiClient();
      const { error } = await api.PUT('/bot-config/{merchant_id}/overrides', {
        params: { path: { merchant_id: merchantId } },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        body: { overrides: { reactivation: values } } as any,
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bot-config', 'overrides', merchantId] });
    },
  });

  const [d, setD] = useState('');
  const [iv, setIv] = useState('');
  const [ma, setMa] = useState('');

  useEffect(() => {
    setD(String(dormantDays));
    setIv(String(intervalDays));
    setMa(String(maxAttempts));
  }, [dormantDays, intervalDays, maxAttempts]);

  const handleSave = () => {
    const dv = parseInt(d, 10);
    const ivv = parseInt(iv, 10);
    const mav = parseInt(ma, 10);
    if (isNaN(dv) || dv < 30 || dv > 180) return;
    if (isNaN(ivv) || ivv < 3 || ivv > 30) return;
    if (isNaN(mav) || mav < 1 || mav > 5) return;
    save.mutate({ dormant_days: dv, interval_days: ivv, max_attempts: mav });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Riattivazione dormienti</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">
          Configura i parametri per ricontattare i lead che non rispondono da tempo.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {configQuery.isLoading ? (
          <p className="text-sm text-muted-foreground">Caricamento…</p>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-1">
              <Label className="text-xs">Giorni dormienza</Label>
              <Input
                type="number"
                min={30}
                max={180}
                value={d}
                onChange={(e) => setD(e.target.value)}
                disabled={save.isPending}
                className="h-8 text-sm"
              />
              <p className="text-xs text-muted-foreground">30–180 giorni</p>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Intervallo tentativi (giorni)</Label>
              <Input
                type="number"
                min={3}
                max={30}
                value={iv}
                onChange={(e) => setIv(e.target.value)}
                disabled={save.isPending}
                className="h-8 text-sm"
              />
              <p className="text-xs text-muted-foreground">3–30 giorni</p>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Max tentativi</Label>
              <Input
                type="number"
                min={1}
                max={5}
                value={ma}
                onChange={(e) => setMa(e.target.value)}
                disabled={save.isPending}
                className="h-8 text-sm"
              />
              <p className="text-xs text-muted-foreground">1–5</p>
            </div>
          </div>
        )}
        <Button size="sm" onClick={handleSave} disabled={save.isPending || configQuery.isLoading}>
          {save.isPending ? 'Salvataggio…' : 'Salva'}
        </Button>
        {save.isSuccess ? (
          <p className="text-xs text-green-600">Salvato.</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function BookingReminderSettings() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [inputHours, setInputHours] = useState('');
  const [inputError, setInputError] = useState('');

  const configQuery = useQuery({
    queryKey: ['bot-config', 'overrides', merchantId],
    enabled: !!merchantId,
    queryFn: async () => {
      if (!merchantId) return null;
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/overrides', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(apiErrorMessage(error));
      return data;
    },
  });

  const currentSchedule: number[] =
    ((configQuery.data?.overrides as ConfigOverrides)?.booking?.reminder_schedule) ?? [24];

  const save = useMutation({
    mutationFn: async (schedule: number[]) => {
      if (!merchantId) throw new Error('Contesto merchant mancante');
      const api = getApiClient();
      const { error } = await api.PUT('/bot-config/{merchant_id}/overrides', {
        params: { path: { merchant_id: merchantId } },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        body: { overrides: { booking: { reminder_schedule: schedule } } } as any,
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bot-config', 'overrides', merchantId] });
    },
  });

  const addHours = () => {
    const h = parseInt(inputHours, 10);
    if (isNaN(h) || h < 1 || h > 168) {
      setInputError('Inserisci un valore tra 1 e 168 ore');
      return;
    }
    if (currentSchedule.includes(h)) {
      setInputError('Questo valore è già presente');
      return;
    }
    if (currentSchedule.length >= 5) {
      setInputError('Massimo 5 promemoria configurabili');
      return;
    }
    setInputError('');
    setInputHours('');
    const newSchedule = [...currentSchedule, h].sort((a, b) => b - a);
    save.mutate(newSchedule);
  };

  const removeHours = (h: number) => {
    const newSchedule = currentSchedule.filter((v) => v !== h);
    save.mutate(newSchedule);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Promemoria appuntamento</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">
          Configura quando inviare il promemoria WhatsApp prima dell&apos;appuntamento. Puoi
          aggiungere fino a 5 orari di anticipo (1–168 ore). Default: 24 ore prima.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {configQuery.isLoading ? (
          <p className="text-sm text-muted-foreground">Caricamento…</p>
        ) : (
          <>
            <div className="flex flex-wrap gap-2">
              {currentSchedule.length === 0 ? (
                <span className="text-sm text-muted-foreground">
                  Nessun promemoria configurato
                </span>
              ) : (
                currentSchedule.map((h) => (
                  <Badge key={h} variant="secondary" className="gap-1 pr-1">
                    {h}h prima
                    <button
                      type="button"
                      onClick={() => removeHours(h)}
                      disabled={save.isPending}
                      className="ml-1 rounded-full p-0.5 hover:bg-muted-foreground/20"
                      aria-label={`Rimuovi ${h}h`}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </Badge>
                ))
              )}
            </div>
            <div className="flex items-end gap-2">
              <div className="flex flex-col gap-1">
                <Label htmlFor="reminder-hours">Aggiungi (ore prima)</Label>
                <Input
                  id="reminder-hours"
                  type="number"
                  min={1}
                  max={168}
                  className="w-28"
                  value={inputHours}
                  onChange={(e) => {
                    setInputHours(e.target.value);
                    setInputError('');
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') addHours();
                  }}
                  placeholder="es. 48"
                />
                {inputError ? (
                  <p className="text-xs text-destructive">{inputError}</p>
                ) : null}
              </div>
              <Button
                variant="outline"
                onClick={addHours}
                disabled={save.isPending || !inputHours}
              >
                Aggiungi
              </Button>
            </div>
            {save.isError ? (
              <p className="text-xs text-destructive">
                Errore:{' '}
                {save.error instanceof Error ? save.error.message : 'sconosciuto'}
              </p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function AutomazioniPanel() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Automation | null>(null);
  const [creating, setCreating] = useState(false);

  const automations = useQuery({
    queryKey: ['automations'],
    queryFn: async (): Promise<Automation[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/automations');
      if (error) throw new Error(apiErrorMessage(error));
      return data as Automation[];
    },
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['automations'] });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const api = getApiClient();
      const { error } = await api.DELETE('/automations/{automation_id}', {
        params: { path: { automation_id: id } },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: invalidate,
  });

  const closeEditor = () => {
    setEditing(null);
    setCreating(false);
  };

  if (creating || editing) {
    return (
      <div className="p-6">
        <AutomationEditor editing={editing} onDone={closeEditor} />
      </div>
    );
  }

  if (automations.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Caricamento automazioni…</div>;
  }
  if (automations.isError) {
    return (
      <div className="p-6 text-sm text-destructive">
        Errore:{' '}
        {automations.error instanceof Error ? automations.error.message : 'sconosciuto'}
      </div>
    );
  }

  const rows = automations.data ?? [];
  const systemRows = rows.filter((a) => a.is_system);
  const customRows = rows.filter((a) => !a.is_system);

  const card = (a: Automation) => {
    const triggerLabel = a.system_key
      ? (TRIGGER_LABEL[a.system_key] ?? a.name)
      : a.trigger_type
        ? (TRIGGER_LABEL[a.trigger_type] ?? a.trigger_type)
        : '—';
    return (
      <Card key={a.id}>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle className="text-base">{a.name}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              {a.is_system ? 'Evento' : 'Trigger'}: {triggerLabel} · {a.nodes.length} nodi
              · {a.edges.length} collegamenti
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {a.is_system ? <Badge variant="outline">Sistema</Badge> : null}
            <Badge variant={a.enabled ? 'success' : 'secondary'}>
              {a.enabled ? 'Attiva' : 'Bozza'}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setEditing(a)}>
              Apri sulla lavagnetta
            </Button>
            {!a.is_system ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => remove.mutate(a.id)}
                disabled={remove.isPending}
              >
                Elimina
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="space-y-6 p-6">
      <NoAnswerSettings />
      <ReactivationSettings />
      <BookingReminderSettings />

      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Tutti i flussi automatici: quelli di sistema (sempre presenti) e quelli personalizzati.
        </p>
        <Button onClick={() => setCreating(true)}>Nuova automazione</Button>
      </div>

      <div className="space-y-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Flussi di sistema
        </p>
        {systemRows.length === 0 ? (
          <Card>
            <CardContent className="py-6 text-center text-sm text-muted-foreground">
              Caricamento flussi di sistema…
            </CardContent>
          </Card>
        ) : (
          systemRows.map(card)
        )}
      </div>

      <div className="space-y-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Automazioni personalizzate
        </p>
        {customRows.length === 0 ? (
          <Card>
            <CardContent className="py-6 text-center text-sm text-muted-foreground">
              Nessuna automazione personalizzata. Creane una: scegli un trigger (es. «Messaggio
              ricevuto»), aggiungi condizioni e azioni sulla lavagnetta.
            </CardContent>
          </Card>
        ) : (
          customRows.map(card)
        )}
      </div>
    </div>
  );
}
