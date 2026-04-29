'use client';

import { EmptyState, ScrollArea, Skeleton } from '@reloop/ui';
import { useVirtualizer } from '@tanstack/react-virtual';
import { MessageSquare } from 'lucide-react';
import { useRef } from 'react';
import type { Conversation } from '../types';
import { ThreadListItem } from './thread-list-item';

interface ThreadListProps {
  conversations: Conversation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  isLoading: boolean;
  error?: Error | null;
}

export function ThreadList({
  conversations,
  selectedId,
  onSelect,
  isLoading,
  error,
}: ThreadListProps) {
  const parentRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: conversations.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 64,
    overscan: 8,
  });

  if (isLoading && conversations.length === 0) {
    return (
      <div className="flex flex-col gap-1 p-3">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3 px-3 py-2.5">
            <Skeleton className="h-10 w-10 rounded-full" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-3 w-48" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6 text-center text-sm text-destructive">
        Errore di caricamento: {error.message}
      </div>
    );
  }

  if (conversations.length === 0) {
    return (
      <EmptyState
        icon={MessageSquare}
        title="Nessuna conversazione"
        description="Le conversazioni appariranno qui non appena un contatto scriverà al tuo numero."
      />
    );
  }

  return (
    <div ref={parentRef} className="h-full overflow-y-auto">
      <div
        style={{
          height: `${rowVirtualizer.getTotalSize()}px`,
          position: 'relative',
          width: '100%',
        }}
      >
        {rowVirtualizer.getVirtualItems().map((vi) => {
          const c = conversations[vi.index];
          if (!c) return null;
          return (
            <div
              key={c.id}
              data-index={vi.index}
              ref={rowVirtualizer.measureElement}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                transform: `translateY(${vi.start}px)`,
              }}
            >
              <ThreadListItem
                conversation={c}
                active={selectedId === c.id}
                onSelect={onSelect}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
