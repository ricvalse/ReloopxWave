'use client';

import { cn, EmptyState, Input } from '@reloop/ui';
import { MessageSquare, Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useConversations } from '../hooks/use-conversations';
import { useSendMessage } from '../hooks/use-send-message';
import { useThread } from '../hooks/use-thread';
import { useConversationsContext } from '../lib/context';
import type { Message } from '../types';
import { Composer } from './composer';
import { MessageList } from './message-list';
import { ThreadHeader } from './thread-header';
import { ThreadList } from './thread-list';

interface ConversationsWorkspaceProps {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

export function ConversationsWorkspace({ selectedId, onSelect }: ConversationsWorkspaceProps) {
  const { composerEnabled } = useConversationsContext();
  const [search, setSearch] = useState('');

  const conversationsQuery = useConversations({ limit: 100 });
  const threadQuery = useThread(selectedId);
  const sendMutation = useSendMessage();

  const conversations = conversationsQuery.data ?? [];

  const filtered = useMemo(() => {
    if (!search.trim()) return conversations;
    const q = search.trim().toLowerCase();
    return conversations.filter((c) => {
      const phone = c.wa_contact_phone?.toLowerCase() ?? '';
      const name = ((c.meta?.['contact_name'] as string | undefined) ?? '').toLowerCase();
      return phone.includes(q) || name.includes(q);
    });
  }, [conversations, search]);

  const selectedConversation = useMemo(
    () => conversations.find((c) => c.id === selectedId) ?? null,
    [conversations, selectedId],
  );

  function handleRetry(failed: Message) {
    if (!failed.client_message_id || !selectedId) return;
    sendMutation.mutate({
      conversationId: selectedId,
      text: failed.content,
      clientMessageId: failed.client_message_id,
    });
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 md:grid-cols-[360px_1fr] xl:grid-cols-[360px_1fr]">
      {/* Thread list rail */}
      <aside
        className={cn(
          'flex min-h-0 flex-col border-r border-border bg-card',
          selectedId ? 'hidden md:flex' : 'flex',
        )}
      >
        <div className="flex h-14 shrink-0 items-center border-b border-border px-4">
          <h2 className="text-sm font-semibold tracking-tight">
            Conversazioni{' '}
            {conversations.length > 0 && (
              <span className="text-muted-foreground">({conversations.length})</span>
            )}
          </h2>
        </div>
        <div className="border-b border-border p-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              placeholder="Cerca contatto…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
        </div>
        <div className="min-h-0 flex-1">
          <ThreadList
            conversations={filtered}
            selectedId={selectedId}
            onSelect={onSelect}
            isLoading={conversationsQuery.isLoading}
            error={conversationsQuery.error as Error | null}
          />
        </div>
      </aside>

      {/* Thread panel */}
      <section
        className={cn(
          'flex min-h-0 min-w-0 flex-col bg-background',
          selectedId ? 'flex' : 'hidden md:flex',
        )}
      >
        {!selectedId || !selectedConversation ? (
          <div className="flex h-full items-center justify-center p-6">
            <EmptyState
              icon={MessageSquare}
              title="Seleziona una conversazione"
              description="Scegli una chat dalla lista per iniziare."
            />
          </div>
        ) : (
          <>
            <ThreadHeader
              conversation={selectedConversation}
              onBack={() => onSelect(null)}
            />
            <div className="min-h-0 flex-1">
              <MessageList
                messages={threadQuery.data ?? []}
                isLoading={threadQuery.isLoading}
                onRetry={handleRetry}
              />
            </div>
            <Composer
              conversationId={selectedId}
              disabled={!composerEnabled}
              disabledReason={
                composerEnabled
                  ? undefined
                  : 'Composer disabilitato per questo merchant. Contatta il supporto.'
              }
            />
          </>
        )}
      </section>
    </div>
  );
}
