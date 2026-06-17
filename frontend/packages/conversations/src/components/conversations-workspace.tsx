'use client';

import { cn, EmptyState, Input, Sheet, SheetContent, SheetTitle } from '@reloop/ui';
import { MessageSquare, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useConversations } from '../hooks/use-conversations';
import { useSendMessage } from '../hooks/use-send-message';
import { useThread } from '../hooks/use-thread';
import { useConversationsContext } from '../lib/context';
import { useResizablePanels } from '../lib/use-resizable-panels';
import type { InboxFilter, Message } from '../types';
import { Composer } from './composer';
import { DetailPanel } from './detail/detail-panel';
import { FilterTabs, matchesInboxFilter } from './filter-tabs';
import { MessageList } from './message-list';
import { PanelResizer } from './panel-resizer';
import { ThreadHeader } from './thread-header';
import { ThreadList } from './thread-list';

interface ConversationsWorkspaceProps {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

/** SSR-safe `max-width: 767px` matcher used to pick column-vs-sheet for the detail panel. */
function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)');
    const update = () => setIsMobile(mq.matches);
    update();
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  }, []);
  return isMobile;
}

export function ConversationsWorkspace({ selectedId, onSelect }: ConversationsWorkspaceProps) {
  const { composerEnabled, customerDetailEnabled = true } = useConversationsContext();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<InboxFilter>('all');
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);

  const isMobile = useIsMobile();
  const panels = useResizablePanels(customerDetailEnabled);

  const conversationsQuery = useConversations({ limit: 100 });
  const threadQuery = useThread(selectedId);
  const sendMutation = useSendMessage();

  const conversations = conversationsQuery.data ?? [];

  // Search first (phone + contact name), then the status-tab predicate. Counts
  // in the tabs reflect the search-filtered set.
  const searchFiltered = useMemo(() => {
    if (!search.trim()) return conversations;
    const q = search.trim().toLowerCase();
    return conversations.filter((c) => {
      const phone = c.wa_contact_phone?.toLowerCase() ?? '';
      const name = ((c.meta?.['contact_name'] as string | undefined) ?? '').toLowerCase();
      return phone.includes(q) || name.includes(q);
    });
  }, [conversations, search]);

  const filtered = useMemo(
    () => searchFiltered.filter((c) => matchesInboxFilter(c, filter)),
    [searchFiltered, filter],
  );

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

  function handleToggleDetail() {
    if (isMobile) {
      setMobileDetailOpen((o) => !o);
    } else {
      panels.toggleDetail();
    }
  }

  const showDesktopDetail =
    customerDetailEnabled && !isMobile && panels.detailOpen && Boolean(selectedConversation);

  return (
    <div className="flex h-full min-h-0 w-full overflow-hidden bg-background">
      {/* Thread-list rail */}
      <aside
        style={!isMobile ? { width: panels.leftWidth, flexShrink: 0 } : undefined}
        className={cn(
          'flex min-h-0 flex-col border-r border-border bg-card',
          selectedId ? 'hidden md:flex' : 'flex md:w-auto',
          isMobile && 'w-full',
        )}
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
          <h2 className="text-sm font-semibold tracking-tight">Conversazioni</h2>
          {conversations.length > 0 && (
            <span className="text-[11px] tabular-nums text-muted-foreground">
              {conversations.length}
            </span>
          )}
        </div>
        <div className="space-y-2 border-b border-border p-3">
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
          <FilterTabs conversations={searchFiltered} value={filter} onChange={setFilter} />
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

      {/* Left resizer (desktop only) */}
      {!isMobile && (
        <PanelResizer
          onMouseDown={panels.startLeftResize}
          active={panels.isResizing}
          aria-label="Ridimensiona la lista conversazioni"
        />
      )}

      {/* Thread panel */}
      <section
        className={cn(
          'flex min-h-0 min-w-0 flex-1 flex-col bg-background',
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
              onToggleDetail={customerDetailEnabled ? handleToggleDetail : undefined}
              detailActive={showDesktopDetail || (isMobile && mobileDetailOpen)}
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
              lastInboundAt={selectedConversation.last_inbound_at}
            />
          </>
        )}
      </section>

      {/* Right resizer + detail rail (desktop only) */}
      {showDesktopDetail && selectedConversation && (
        <>
          <PanelResizer
            onMouseDown={panels.startRightResize}
            active={panels.isResizing}
            aria-label="Ridimensiona il pannello dettagli"
          />
          <aside
            style={{ width: panels.rightWidth, flexShrink: 0 }}
            className="hidden min-h-0 border-l border-border md:flex md:flex-col"
          >
            <DetailPanel conversation={selectedConversation} onClose={panels.toggleDetail} />
          </aside>
        </>
      )}

      {/* Detail panel as a sheet on mobile */}
      {customerDetailEnabled && selectedConversation && (
        <Sheet open={isMobile && mobileDetailOpen} onOpenChange={setMobileDetailOpen}>
          <SheetContent side="right" className="w-[88%] max-w-sm p-0 sm:max-w-md">
            <SheetTitle className="sr-only">Dettagli contatto</SheetTitle>
            <DetailPanel
              conversation={selectedConversation}
              onClose={() => setMobileDetailOpen(false)}
              hideClose
            />
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}
