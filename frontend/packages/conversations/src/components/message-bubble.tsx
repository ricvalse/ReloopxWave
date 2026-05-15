'use client';

import { cn } from '@reloop/ui';
import { memo } from 'react';
import { formatBubbleTime } from '../lib/time';
import type { Message } from '../types';
import { StatusTicks } from './status-ticks';

interface MessageBubbleProps {
  message: Message;
  /** True when the previous bubble in the list shares sender/role within ~2 min. */
  grouped: boolean;
  /** Show retry affordance under the bubble. */
  onRetry?: (message: Message) => void;
}

// Reserves last-line space at the end of the text so the absolutely-positioned
// timestamp+ticks don't overlap the final word — same trick WhatsApp uses.
// Width is conservative for "12:34 ✓✓" plus padding.
const META_SPACER_WIDTH = 68;
const META_SPACER_WIDTH_NO_TICKS = 44;

function MessageBubbleImpl({ message, grouped, onRetry }: MessageBubbleProps) {
  const isOut = message.direction === 'out';
  const isAgent = message.role === 'agent';
  const isFailed = message.status === 'failed';
  const isFromPhone = message.meta?.sender_type === 'phone';
  const showTicks = isOut;
  const spacer = showTicks ? META_SPACER_WIDTH : META_SPACER_WIDTH_NO_TICKS;

  return (
    <div
      className={cn(
        'flex w-full px-3 sm:px-6',
        isOut ? 'justify-end' : 'justify-start',
        grouped ? 'mt-0.5' : 'mt-2',
      )}
    >
      <div
        className={cn(
          'relative max-w-[85%] rounded-2xl px-2.5 pb-1.5 pt-1.5 text-sm shadow-[0_1px_0.5px_rgba(0,0,0,0.13)] sm:max-w-[68%]',
          isOut
            ? cn(
                // Outbound: brand-tinted pastel for agent, plain card for assistant.
                isAgent
                  ? 'bg-[oklch(var(--chat-bubble-out))] text-[oklch(var(--chat-bubble-out-fg))]'
                  : 'bg-[oklch(var(--chat-bubble-in))] text-foreground',
                // Tail only on group leader.
                !grouped && 'rounded-tr-[4px]',
              )
            : cn(
                'bg-[oklch(var(--chat-bubble-in))] text-foreground',
                !grouped && 'rounded-tl-[4px]',
              ),
          isFailed && 'opacity-80',
        )}
      >
        {isFromPhone && (
          <span
            className={cn(
              'mb-0.5 block text-[10px] font-medium uppercase tracking-wide',
              'text-[oklch(var(--chat-meta))]',
            )}
          >
            Da telefono
          </span>
        )}
        <span className="whitespace-pre-wrap break-words leading-relaxed">
          {message.content}
          {/* Last-line meta spacer: invisible inline block reserving room
              for the absolutely-positioned timestamp+ticks. */}
          <span
            aria-hidden
            className="inline-block h-[1px] align-baseline"
            style={{ width: spacer }}
          />
        </span>
        <span
          className={cn(
            'absolute bottom-1 right-2 inline-flex select-none items-center gap-1 text-[10px] tabular-nums',
            'text-[oklch(var(--chat-meta))]',
          )}
        >
          <span>{formatBubbleTime(message.created_at)}</span>
          {showTicks && <StatusTicks status={message.status} className="-mb-px" />}
        </span>
      </div>

      {isFailed && onRetry && (
        <button
          onClick={() => onRetry(message)}
          className="ml-2 self-end text-[11px] font-medium text-destructive underline-offset-2 hover:underline"
        >
          Riprova
        </button>
      )}
    </div>
  );
}

export const MessageBubble = memo(MessageBubbleImpl, (prev, next) => {
  return (
    prev.message.id === next.message.id &&
    prev.message.status === next.message.status &&
    prev.message.read_at === next.message.read_at &&
    prev.message.delivered_at === next.message.delivered_at &&
    prev.message.meta?.sender_type === next.message.meta?.sender_type &&
    prev.grouped === next.grouped
  );
});
