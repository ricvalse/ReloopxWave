'use client';

import { Skeleton } from '@reloop/ui';
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { isSameDay } from '../lib/time';
import type { Message } from '../types';
import { ChatWallpaper } from './chat-wallpaper';
import { DaySeparator } from './day-separator';
import { MessageBubble } from './message-bubble';

interface MessageListProps {
  messages: Message[];
  isLoading: boolean;
  onRetry?: (message: Message) => void;
}

const GROUPING_WINDOW_MS = 2 * 60 * 1000;

type RenderItem =
  | { kind: 'separator'; key: string; iso: string }
  | { kind: 'bubble'; key: string; message: Message; grouped: boolean };

function buildItems(messages: Message[]): RenderItem[] {
  const items: RenderItem[] = [];
  let lastDayIso: string | null = null;
  let prev: Message | null = null;

  for (const m of messages) {
    if (!lastDayIso || !isSameDay(lastDayIso, m.created_at)) {
      items.push({ kind: 'separator', key: `sep:${m.created_at}`, iso: m.created_at });
      lastDayIso = m.created_at;
      prev = null;
    }
    const grouped =
      !!prev &&
      prev.role === m.role &&
      prev.direction === m.direction &&
      new Date(m.created_at).getTime() - new Date(prev.created_at).getTime() < GROUPING_WINDOW_MS;
    items.push({ kind: 'bubble', key: m.id, message: m, grouped });
    prev = m;
  }
  return items;
}

export function MessageList({ messages, isLoading, onRetry }: MessageListProps) {
  const items = useMemo(() => buildItems(messages), [messages]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastIdRef = useRef<string | null>(null);

  // Auto-scroll to bottom when a new message arrives, but only if the user
  // is already near the bottom — preserve their scroll if they're reading older.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const lastId = messages[messages.length - 1]?.id ?? null;
    if (lastId === lastIdRef.current) return;
    const isFirstLoad = lastIdRef.current === null;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const nearBottom = distanceFromBottom < 200;
    if (isFirstLoad || nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
    lastIdRef.current = lastId;
  }, [messages]);

  // Reset on conversation change (first message id changes radically)
  useEffect(() => {
    lastIdRef.current = null;
  }, [messages[0]?.conversation_id]);

  if (isLoading && messages.length === 0) {
    return (
      <div className="chat-surface relative h-full overflow-hidden bg-[oklch(var(--chat-wallpaper-bg))]">
        <ChatWallpaper />
        <div className="relative z-10 flex h-full flex-col gap-3 p-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className={i % 2 === 0 ? 'self-start' : 'self-end'}>
              <Skeleton className="h-10 w-64 rounded-2xl" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="chat-surface relative flex h-full items-center justify-center bg-[oklch(var(--chat-wallpaper-bg))] text-sm text-muted-foreground">
        <ChatWallpaper />
        <span className="relative z-10 rounded-full bg-background/80 px-3 py-1 shadow-sm">
          Nessun messaggio ancora.
        </span>
      </div>
    );
  }

  return (
    <div
      ref={scrollRef}
      className="chat-surface relative h-full overflow-y-auto bg-[oklch(var(--chat-wallpaper-bg))] pb-4"
    >
      <ChatWallpaper />
      <div className="relative z-10 mx-auto max-w-3xl py-2">
        {items.map((item) =>
          item.kind === 'separator' ? (
            <DaySeparator key={item.key} iso={item.iso} />
          ) : (
            <MessageBubble
              key={item.key}
              message={item.message}
              grouped={item.grouped}
              onRetry={onRetry}
            />
          ),
        )}
      </div>
    </div>
  );
}
