'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getBrowserSupabase } from '@/lib/supabase';

type Conversation = {
  id: string;
  wa_contact_phone: string | null;
  status: string;
  last_message_at: string | null;
  message_count: number;
  meta: Record<string, unknown> | null;
};

type Message = {
  id: string;
  conversation_id: string;
  role: string;
  content: string;
  created_at: string;
};

export function ConversationsPanel() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const conversations = useQuery({
    queryKey: ['conversations', 'list'],
    queryFn: async (): Promise<Conversation[]> => {
      const supabase = getBrowserSupabase();
      const { data, error } = await supabase
        .from('conversations')
        .select('id, wa_contact_phone, status, last_message_at, message_count, meta')
        .order('last_message_at', { ascending: false, nullsFirst: false })
        .limit(100);
      if (error) throw new Error(error.message);
      return (data ?? []) as Conversation[];
    },
    // Polling fallback in case Realtime drops the connection or the
    // publication isn't broadcasting. Cheap (single SELECT, RLS-scoped).
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  });

  // Realtime — any message insert for this merchant bumps the list + open thread.
  useEffect(() => {
    const supabase = getBrowserSupabase();
    const channel = supabase
      .channel('conversations-viewer')
      .on(
        'postgres_changes' as never,
        { event: 'INSERT', schema: 'public', table: 'messages' } as never,
        (payload: { new: Message }) => {
          void queryClient.invalidateQueries({ queryKey: ['conversations', 'list'] });
          void queryClient.invalidateQueries({
            queryKey: ['conversations', 'thread', payload.new.conversation_id],
          });
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [queryClient]);

  return (
    <div className="grid h-[calc(100vh-7rem)] gap-4 p-6 md:grid-cols-[1fr_2fr]">
      <Card className="flex min-h-0 flex-col">
        <CardHeader className="shrink-0">
          <CardTitle>Conversazioni ({conversations.data?.length ?? 0})</CardTitle>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 overflow-y-auto p-0">
          {conversations.isLoading ? (
            <p className="px-6 py-4 text-sm text-muted-foreground">Caricamento…</p>
          ) : conversations.isError ? (
            <p className="px-6 py-4 text-sm text-destructive">
              {conversations.error instanceof Error
                ? conversations.error.message
                : 'Errore di lettura'}
            </p>
          ) : (conversations.data ?? []).length === 0 ? (
            <p className="px-6 py-4 text-sm text-muted-foreground">
              Nessuna conversazione ancora. Le nuove appariranno in tempo reale.
            </p>
          ) : (
            <ul className="divide-y">
              {(conversations.data ?? []).map((c) => (
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
                    <span className="font-mono text-sm">{c.wa_contact_phone ?? '—'}</span>
                    <span className="text-xs text-muted-foreground">
                      {c.message_count} msg · {c.status} ·{' '}
                      {c.last_message_at
                        ? new Date(c.last_message_at).toLocaleString('it-IT')
                        : 'mai'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card className="flex min-h-0 flex-col">
        <CardHeader className="shrink-0">
          <CardTitle>Messaggi</CardTitle>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 overflow-y-auto">
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
  );
}

function ThreadView({ conversationId }: { conversationId: string }) {
  const thread = useQuery({
    queryKey: ['conversations', 'thread', conversationId],
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
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  });

  const items = useMemo(() => thread.data ?? [], [thread.data]);

  // Auto-scroll the thread to the latest message whenever a new one
  // appears. Anchored to the bottom sentinel so behaviour is identical
  // for first render and live updates.
  const bottomRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [items.length]);

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
      <div ref={bottomRef} />
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
