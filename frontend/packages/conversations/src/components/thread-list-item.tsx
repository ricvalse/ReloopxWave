'use client';

import { Avatar, AvatarFallback, Badge, cn } from '@reloop/ui';
import { memo } from 'react';
import { contactDisplayName, contactInitials } from '../lib/initials';
import { formatThreadTime } from '../lib/time';
import type { Conversation } from '../types';

interface ThreadListItemProps {
  conversation: Conversation;
  active: boolean;
  onSelect: (id: string) => void;
}

function ThreadListItemImpl({ conversation, active, onSelect }: ThreadListItemProps) {
  const phone = conversation.wa_contact_phone;
  const name = (conversation.meta?.['contact_name'] as string | undefined) ?? null;
  const display = contactDisplayName(name, phone);
  const initials = contactInitials(name, phone);
  const role = conversation.last_message_role;
  const rawPreview = conversation.last_message_preview ?? null;
  const previewPrefix = rawPreview
    ? role === 'agent'
      ? 'Tu: '
      : role === 'assistant'
        ? 'Bot: '
        : ''
    : '';
  const time = formatThreadTime(conversation.last_message_at);
  const unread = conversation.unread_count ?? 0;
  const showStatusChip =
    !rawPreview && unread === 0 && conversation.status !== 'active';

  return (
    <button
      onClick={() => onSelect(conversation.id)}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group flex w-full items-center gap-3 px-3 py-3 text-left transition-colors',
        'border-b border-[oklch(var(--chat-tile-rule))]',
        active ? 'bg-accent/70' : 'hover:bg-accent/40',
      )}
    >
      <Avatar className="h-12 w-12 shrink-0">
        <AvatarFallback className="text-[12px] font-semibold">{initials}</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-baseline gap-2">
          <span
            className={cn(
              'truncate text-[14px]',
              unread > 0 ? 'font-semibold text-foreground' : 'font-medium text-foreground',
            )}
          >
            {display}
          </span>
          <span
            className={cn(
              'ml-auto shrink-0 text-[11px] tabular-nums',
              unread > 0 ? 'text-primary' : 'text-muted-foreground',
            )}
          >
            {time}
          </span>
        </div>
        <div className="mt-0.5 flex items-center gap-2">
          <span
            className={cn(
              'flex-1 truncate text-[13px]',
              unread > 0 ? 'text-foreground/80' : 'text-muted-foreground',
            )}
          >
            {rawPreview ? (
              <>
                {previewPrefix && (
                  <span className="text-muted-foreground/80">{previewPrefix}</span>
                )}
                {rawPreview}
              </>
            ) : (
              <span className="italic text-muted-foreground/70">
                {phone ?? 'Nessun messaggio'}
              </span>
            )}
          </span>
          {unread > 0 ? (
            <Badge
              variant="default"
              className="h-5 min-w-5 shrink-0 justify-center px-1.5 text-[10px]"
            >
              {unread > 99 ? '99+' : unread}
            </Badge>
          ) : showStatusChip ? (
            <span className="shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground">
              {conversation.status}
            </span>
          ) : null}
        </div>
      </div>
    </button>
  );
}

export const ThreadListItem = memo(ThreadListItemImpl);
