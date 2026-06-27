'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Button,
  ButtonSpinner,
  Card,
  CardContent,
  Input,
  Label,
  SkeletonCard,
  Switch,
} from '@reloop/ui';
import { ArrowDownToLine } from 'lucide-react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

const DAYS = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'];

type DayRow = {
  day_of_week: number;
  is_open: boolean;
  open_time: string;
  close_time: string;
  break_start: string;
  break_end: string;
};

type HourFromApi = {
  id: string;
  day_of_week: number;
  is_open: boolean;
  open_time: string | null;
  close_time: string | null;
  break_start: string | null;
  break_end: string | null;
};

function defaultRows(): DayRow[] {
  return DAYS.map((_, i) => ({
    day_of_week: i,
    is_open: i < 5,
    open_time: '09:00',
    close_time: '18:00',
    break_start: '',
    break_end: '',
  }));
}

function fromApi(rows: HourFromApi[]): DayRow[] {
  const base = defaultRows();
  for (const r of rows) {
    base[r.day_of_week] = {
      day_of_week: r.day_of_week,
      is_open: r.is_open,
      open_time: r.open_time?.slice(0, 5) ?? '09:00',
      close_time: r.close_time?.slice(0, 5) ?? '18:00',
      break_start: r.break_start?.slice(0, 5) ?? '',
      break_end: r.break_end?.slice(0, 5) ?? '',
    };
  }
  return base;
}

export function BusinessHoursPanel() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<DayRow[]>(defaultRows);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['business-hours', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<HourFromApi[]> => {
      if (!merchantId) return [];
      const api = getApiClient();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { data, error } = await (api as any).GET('/services/{merchant_id}/hours', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(apiErrorMessage(error));
      return (data as HourFromApi[]) ?? [];
    },
  });

  useEffect(() => {
    if (query.data) setRows(fromApi(query.data));
  }, [query.data]);

  const syncFromGhl = useMutation({
    mutationFn: async () => {
      if (!merchantId) throw new Error('Merchant context mancante');
      const api = getApiClient();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { data, error } = await (api as any).POST('/services/{merchant_id}/sync-from-ghl', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(apiErrorMessage(error));
      return data as { hours_imported: number; closures_imported: number };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['business-hours', merchantId] });
      queryClient.invalidateQueries({ queryKey: ['business-closures', merchantId] });
      setSyncError(null);
    },
    onError: (e: Error) => setSyncError(e.message),
  });

  const save = useMutation({
    mutationFn: async () => {
      if (!merchantId) throw new Error('Merchant context mancante');
      const api = getApiClient();
      const payload = rows.map((r) => ({
        day_of_week: r.day_of_week,
        is_open: r.is_open,
        open_time: r.is_open && r.open_time ? r.open_time : null,
        close_time: r.is_open && r.close_time ? r.close_time : null,
        break_start: r.is_open && r.break_start ? r.break_start : null,
        break_end: r.is_open && r.break_end ? r.break_end : null,
      }));
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { error } = await (api as any).PUT('/services/{merchant_id}/hours', {
        params: { path: { merchant_id: merchantId } },
        body: payload,
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['business-hours', merchantId] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
    onError: (e: Error) => setSaveError(e.message),
  });

  const update = (i: number, patch: Partial<DayRow>) => {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  };

  if (query.isPending) return <SkeletonCard />;

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold">Orari di apertura</h2>
          <p className="text-sm text-muted-foreground">
            Il bot usa questi orari per validare e proporre gli slot di prenotazione.
            Modifiche salvate qui vengono spinte automaticamente al calendario GHL.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => syncFromGhl.mutate()}
          disabled={syncFromGhl.isPending}
          className="shrink-0"
        >
          {syncFromGhl.isPending ? (
            <ButtonSpinner />
          ) : (
            <ArrowDownToLine className="mr-2 h-4 w-4" />
          )}
          Importa da GHL
        </Button>
      </div>
      {syncError && <p className="text-sm text-destructive">{syncError}</p>}
      {syncFromGhl.isSuccess && (
        <p className="text-sm text-green-600">
          Importati {syncFromGhl.data?.hours_imported} orari e{' '}
          {syncFromGhl.data?.closures_imported} chiusure da GHL.
        </p>
      )}
      <Card>
        <CardContent className="p-0">
          <div className="divide-y">
            {rows.map((row, i) => (
              <div key={row.day_of_week} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="w-24 shrink-0">
                  <Switch
                    checked={row.is_open}
                    onCheckedChange={(v) => update(i, { is_open: v })}
                  />
                  <span className="ml-2 text-sm font-medium">{DAYS[i]}</span>
                </div>
                {row.is_open ? (
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <div className="flex items-center gap-1">
                      <Label className="text-xs text-muted-foreground">Dalle</Label>
                      <Input
                        type="time"
                        value={row.open_time}
                        onChange={(e) => update(i, { open_time: e.target.value })}
                        className="h-7 w-28 text-sm"
                      />
                    </div>
                    <div className="flex items-center gap-1">
                      <Label className="text-xs text-muted-foreground">alle</Label>
                      <Input
                        type="time"
                        value={row.close_time}
                        onChange={(e) => update(i, { close_time: e.target.value })}
                        className="h-7 w-28 text-sm"
                      />
                    </div>
                    <div className="flex items-center gap-1 border-l pl-2">
                      <Label className="text-xs text-muted-foreground">Pausa</Label>
                      <Input
                        type="time"
                        value={row.break_start}
                        onChange={(e) => update(i, { break_start: e.target.value })}
                        className="h-7 w-28 text-sm"
                        placeholder="—"
                      />
                      <span className="text-xs text-muted-foreground">–</span>
                      <Input
                        type="time"
                        value={row.break_end}
                        onChange={(e) => update(i, { break_end: e.target.value })}
                        className="h-7 w-28 text-sm"
                        placeholder="—"
                      />
                    </div>
                  </div>
                ) : (
                  <span className="text-sm text-muted-foreground">Chiuso</span>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
      {saveError && <p className="text-sm text-destructive">{saveError}</p>}
      <div className="flex justify-end">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending && <ButtonSpinner />}
          {saved ? 'Salvato ✓' : 'Salva orari'}
        </Button>
      </div>
    </div>
  );
}
