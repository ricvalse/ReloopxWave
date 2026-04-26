'use client';

import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { getBrowserSupabase } from '@/lib/supabase';

type Merchant = components['schemas']['MerchantOut'];

type Conversation = {
  id: string;
  merchant_id: string;
  wa_contact_phone: string | null;
  status: string;
  last_message_at: string | null;
  message_count: number;
};

type Message = {
  id: string;
  conversation_id: string;
  role: string;
  content: string;
  created_at: string;
};

const ALL_MERCHANTS = '__all__';

export function InboxPanel() {
  const queryClient = useQueryClient();
  const [merchantFilter, setMerchantFilter] = useState<string>(ALL_MERCHANTS);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const merchants = useQuery({
    queryKey: ['inbox', 'merchants'],
    queryFn: async (): Promise<Merchant[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/merchants/' as never, {} as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant[];
    },
  });

  const merchantById = useMemo(() => {
    const m = new Map<string, Merchant>();
    for (const x of merchants.data ?? []) m.set(x.id, x);
    return m;
  }, [merchants.data]);

  const conversations = useQuery({
    queryKey: ['inbox', 'conversations', merchantFilter],
    queryFn: async (): Promise<Conversation[]> => {
      const supabase = getBrowserSupabase();
      let query = supabase
        .from('conversations')
        .select('id, merchant_id, wa_contact_phone, status, last_message_at, message_count')
        .order('last_message_at', { ascending: false, nullsFirst: false })
        .limit(200);
      if (merchantFilter !== ALL_MERCHANTS) {
        query = query.eq('merchant_id', merchantFilter);
      }
      const { data, error } = await query;
      if (error) throw new Error(error.message);
      return (data ?? []) as Conversation[];
    },
  });

  // Realtime — any new message anywhere in the tenant bumps the relevant
  // queries. RLS on `messages` already filters to the agency_admin's tenant
  // so we don't risk leaking from another tenant via the channel.
  useEffect(() => {
    const supabase = getBrowserSupabase();
    const channel = supabase
      .channel('admin-inbox')
      .on(
        'postgres_changes' as never,
        { event: 'INSERT', schema: 'public', table: 'messages' } as never,
        (payload: { new: Message }) => {
          void queryClient.invalidateQueries({ queryKey: ['inbox', 'conversations'] });
          void queryClient.invalidateQueries({
            queryKey: ['inbox', 'thread', payload.new.conversation_id],
          });
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [queryClient]);

  const list = conversations.data ?? [];

  return (
    <div className="space-y-3 p-6">
      <div className="flex items-center gap-2">
        <label htmlFor="merchant-filter" className="text-sm text-muted-foreground">
          Merchant
        </label>
        <select
          id="merchant-filter"
          value={merchantFilter}
          onChange={(e) => {
            setMerchantFilter(e.target.value);
            setSelectedId(null);
          }}
          disabled={merchants.isLoading}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value={ALL_MERCHANTS}>Tutti i merchant</option>
          {(merchants.data ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
      </div>

      <div className="grid gap-4 md:grid-cols-[1fr_2fr]">
        <Card className="min-h-[60vh]">
          <CardHeader>
            <CardTitle>Conversazioni ({list.length})</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {conversations.isLoading ? (
              <p className="px-6 py-4 text-sm text-muted-foreground">Caricamento…</p>
            ) : conversations.isError ? (
              <p className="px-6 py-4 text-sm text-destructive">
                {conversations.error instanceof Error
                  ? conversations.error.message
                  : 'Errore di lettura'}
              </p>
            ) : list.length === 0 ? (
              <p className="px-6 py-4 text-sm text-muted-foreground">
                {merchantFilter === ALL_MERCHANTS
                  ? 'Nessuna conversazione ancora. Le nuove appariranno in tempo reale.'
                  : 'Nessuna conversazione per questo merchant.'}
              </p>
            ) : (
              <ul className="divide-y">
                {list.map((c) => {
                  const merchant = merchantById.get(c.merchant_id);
                  const merchantLabel = merchant?.name ?? c.merchant_id.slice(0, 8);
                  return (
                    <li key={c.id}>
                      <button
                        type="button"
                        onClick={() => setSelectedId(c.id)}
                        className={
                          selectedId === c.id
                            ? 'flex w-full flex-col items-start gap-0.5 bg-accent px-6 py-3 text-left'
                            : 'flex w-full flex-col items-start gap-0.5 px-6 py-3 text-left hover:bg-accent/40'
                        }
                      >
                        <div className="flex w-full items-center justify-between gap-2">
                          <span className="font-mono text-sm">{c.wa_contact_phone ?? '—'}</span>
                          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                            {merchantLabel}
                          </span>
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {c.message_count} msg · {c.status} ·{' '}
                          {c.last_message_at
                            ? new Date(c.last_message_at).toLocaleString('it-IT')
                            : 'mai'}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card className="min-h-[60vh]">
          <CardHeader>
            <CardTitle>Messaggi</CardTitle>
          </CardHeader>
          <CardContent>
            {selectedId ? (
              <ThreadView conversationId={selectedId} />
            ) : (
              <p className="text-sm text-muted-foreground">
                Seleziona una conversazione per vedere il thread.
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function ThreadView({ conversationId }: { conversationId: string }) {
  const thread = useQuery({
    queryKey: ['inbox', 'thread', conversationId],
    queryFn: async (): Promise<Message[]> => {
      const supabase = getBrowserSupabase();
      const { data, error } = await supabase
        .from('messages')
        .select('id, conversation_id, role, content, created_at')
        .eq('conversation_id', conversationId)
        .order('created_at', { ascending: true })
        .limit(500);
      if (error) throw new Error(error.message);
      return (data ?? []) as Message[];
    },
  });

  const items = useMemo(() => thread.data ?? [], [thread.data]);

  if (thread.isLoading) return <p className="text-sm text-muted-foreground">Caricamento…</p>;
  if (thread.isError) {
    return (
      <p className="text-sm text-destructive">
        {thread.error instanceof Error ? thread.error.message : 'Errore'}
      </p>
    );
  }
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">Nessun messaggio.</p>;
  }

  return (
    <div className="space-y-2">
      {items.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isAssistant = message.role === 'assistant';
  return (
    <div className={isAssistant ? 'flex justify-start' : 'flex justify-end'}>
      <div
        className={
          isAssistant
            ? 'max-w-[75%] rounded-lg bg-muted px-3 py-2 text-sm'
            : 'max-w-[75%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground'
        }
      >
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
        <p
          className={
            isAssistant
              ? 'mt-1 text-[10px] text-muted-foreground'
              : 'mt-1 text-[10px] text-primary-foreground/70'
          }
        >
          {new Date(message.created_at).toLocaleString('it-IT')}
        </p>
      </div>
    </div>
  );
}
