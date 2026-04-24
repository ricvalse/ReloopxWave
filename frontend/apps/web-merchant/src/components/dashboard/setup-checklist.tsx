'use client';

import Link from 'next/link';
import type { Route } from 'next';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type IntegrationStatus = {
  connections: { provider: string; connected: boolean; status: string }[];
};

type ResolvedConfig = {
  business?: {
    name?: string | null;
    industry?: string | null;
    description?: string | null;
  };
  booking?: { default_calendar_id?: string | null };
  pipeline?: { qualified_stage_id?: string | null };
};

type KbList = { items?: { id: string }[] };

type Item = {
  key: string;
  label: string;
  description: string;
  href: Route;
  done: boolean;
};

function asString(v: unknown): string {
  return typeof v === 'string' ? v.trim() : '';
}

export function SetupChecklist() {
  const { merchantId } = useMerchantId();

  const statusQuery = useQuery({
    queryKey: ['setup', 'integrations', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<IntegrationStatus> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/integrations/status' as never, {} as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as IntegrationStatus;
    },
  });

  const configQuery = useQuery({
    queryKey: ['setup', 'config', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<ResolvedConfig> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/resolved' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as ResolvedConfig;
    },
  });

  const kbQuery = useQuery({
    queryKey: ['setup', 'kb', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<KbList> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/knowledge-base/{merchant_id}/docs' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as KbList;
    },
  });

  if (!merchantId) return null;
  if (statusQuery.isLoading || configQuery.isLoading || kbQuery.isLoading) return null;

  const conns = statusQuery.data?.connections ?? [];
  const byProv = Object.fromEntries(conns.map((c) => [c.provider, c]));
  const business = configQuery.data?.business ?? {};
  const booking = configQuery.data?.booking ?? {};
  const pipeline = configQuery.data?.pipeline ?? {};
  const kbCount = kbQuery.data?.items?.length ?? 0;

  const items: Item[] = [
    {
      key: 'wa',
      label: 'Collega WhatsApp',
      description: 'Necessario per ricevere e inviare messaggi.',
      href: '/integrations',
      done: !!byProv['whatsapp']?.connected,
    },
    {
      key: 'ghl',
      label: 'Collega GoHighLevel',
      description: 'Serve per upsertare contatti, muovere pipeline e prenotare.',
      href: '/integrations',
      done: !!byProv['ghl']?.connected,
    },
    {
      key: 'business',
      label: 'Compila il profilo attività',
      description: 'Nome, settore, descrizione, offerta — il bot parla a nome tuo.',
      href: '/bot/config',
      done:
        asString(business.name).length > 0 &&
        asString(business.industry).length > 0 &&
        asString(business.description).length > 0,
    },
    {
      key: 'calendar',
      label: 'Imposta il calendario GHL di default',
      description: 'Senza questo, il bot non può prenotare slot (UC-02).',
      href: '/bot/config',
      done: asString(booking.default_calendar_id).length > 0,
    },
    {
      key: 'stage',
      label: 'Imposta lo stage “qualified” della pipeline GHL',
      description: 'Necessario per l’avanzamento automatico delle opportunità (UC-04).',
      href: '/bot/config',
      done: asString(pipeline.qualified_stage_id).length > 0,
    },
    {
      key: 'kb',
      label: 'Carica almeno un documento nella Knowledge Base',
      description: 'Opzionale, ma permette al bot di citare i tuoi materiali (UC-07).',
      href: '/bot/knowledge-base',
      done: kbCount > 0,
    },
  ];

  const doneCount = items.filter((i) => i.done).length;
  if (doneCount === items.length) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Pronto per partire ({doneCount}/{items.length})</CardTitle>
        <p className="text-sm text-muted-foreground">
          Completa questi passaggi perché il bot funzioni davvero end-to-end.
        </p>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={item.key} className="flex items-start gap-3">
              <span
                aria-hidden
                className={
                  'mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs ' +
                  (item.done
                    ? 'bg-emerald-100 text-emerald-700 ring-1 ring-inset ring-emerald-200'
                    : 'bg-muted text-muted-foreground ring-1 ring-inset ring-border')
                }
              >
                {item.done ? '✓' : ''}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    href={item.href}
                    className={
                      'text-sm font-medium hover:underline ' +
                      (item.done ? 'text-muted-foreground line-through' : 'text-foreground')
                    }
                  >
                    {item.label}
                  </Link>
                </div>
                <p className="text-xs text-muted-foreground">{item.description}</p>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
