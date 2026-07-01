'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo } from 'react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';
import { getBrowserSupabase } from '@/lib/supabase';

export type Appointment = {
  id: string;
  merchant_id: string;
  lead_id: string | null;
  ghl_appointment_id: string | null;
  ghl_contact_id: string | null;
  calendar_id: string | null;
  title: string | null;
  start_at: string;
  end_at: string | null;
  tz_name: string | null;
  status: string;
  source: string;
  meta: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  // Relazioni embedded (PostgREST) — leggibili sotto la stessa RLS merchant.
  // `null` quando lead_id / service_id sono NULL (es. appuntamenti legacy o da GHL).
  lead: { name: string | null } | null;
  service: { name: string | null } | null;
};

/** Nome della persona dell'appuntamento, dal lead collegato (può mancare se il
 *  bot non ha ancora raccolto il nome). */
export function appointmentPersonName(a: Appointment): string | null {
  const n = a.lead?.name?.trim();
  return n ? n : null;
}

/** Servizio prenotato: dalla relazione `service`, con fallback al `title`
 *  denormalizzato (impostato al nome del servizio in fase di booking, e usato
 *  anche dagli appuntamenti GHL il cui titolo arriva dal calendario). */
export function appointmentServiceName(a: Appointment): string | null {
  const s = a.service?.name?.trim() || a.title?.trim();
  return s ? s : null;
}

const LIST_KEY = ['appointments', 'list'] as const;
const COLUMNS =
  'id, merchant_id, lead_id, ghl_appointment_id, ghl_contact_id, calendar_id, title, start_at, end_at, tz_name, status, source, meta, created_at, updated_at, lead:leads(name), service:services(name)';
const REALTIME_FALLBACK_MS = 30_000;

/** Agenda list — read straight from Supabase under RLS (the mirror table is
 *  merchant-scoped), with Realtime as canonical and a 30s poll as the
 *  circuit-breaker fallback (matches the conversations list). */
export function useAppointments() {
  const supabase = useMemo(() => getBrowserSupabase(), []);
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: [...LIST_KEY, merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<Appointment[]> => {
      let req = supabase.from('appointments').select(COLUMNS).order('start_at', { ascending: true });
      if (merchantId) req = req.eq('merchant_id', merchantId);
      const { data, error } = await req;
      if (error) throw error;
      return (data ?? []) as unknown as Appointment[];
    },
    refetchInterval: REALTIME_FALLBACK_MS,
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    const channel = supabase
      .channel('appointments:list')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'appointments' },
        () => {
          void queryClient.invalidateQueries({ queryKey: LIST_KEY });
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [supabase, queryClient]);

  return query;
}

export function useRescheduleAppointment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { id: string; startAtIso: string; endAtIso?: string | null }) => {
      const api = getApiClient();
      const { error } = await api.POST('/appointments/{appointment_id}/reschedule', {
        params: { path: { appointment_id: vars.id } },
        body: { start_at_iso: vars.startAtIso, end_at_iso: vars.endAtIso ?? null },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
}

export function useCancelAppointment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const api = getApiClient();
      const { error } = await api.POST('/appointments/{appointment_id}/cancel', {
        params: { path: { appointment_id: id } },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: LIST_KEY });
    },
  });
}
