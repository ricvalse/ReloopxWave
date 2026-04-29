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

function MessageBubbleImpl({ message, grouped, onRetry }: MessageBubbleProps) {
  const isOut = message.direction === 'out';
  const isAgent = message.role === 'agent';
  const isAssistant = message.role === 'assistant';

  return (
    <div
      className={cn(
        'flex w-full px-4',
        isOut ? 'justify-end' : 'justify-start',
        grouped ? 'mt-0.5' : 'mt-3',
      )}
    >
      <div
        className={cn(
          'flex max-w-[85%] flex-col gap-1 rounded-2xl px-3 py-2 text-sm shadow-sm sm:max-w-[70%]',
          isOut
            ? cn(
                'rounded-tr-sm',
                isAgent
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-card-elevated text-foreground border border-border',
              )
            : 'rounded-tl-sm bg-card-elevated text-foreground border border-border',
          message.status === 'failed' && 'opacity-80',
        )}
      >
        {/* Sender chip — only for outbound bot vs human distinction */}
        {!grouped && isOut && isAssistant && (
          <span className="text-[10px] font-medium uppercase tracking-wider opacity-70">
            Bot
          </span>
        )}
        {!grouped && isOut && isAgent && (
          <span className="text-[10px] font-medium uppercase tracking-wider opacity-80">
            Tu
          </span>
        )}

        <div className="whitespace-pre-wrap break-words leading-relaxed">{message.content}</div>

        <div
          className={cn(
            'mt-0.5 flex items-center justify-end gap-1 text-[10px] tabular-nums',
            isOut && isAgent ? 'opacity-80' : 'opacity-60',
          )}
        >
          <span>{formatBubbleTime(message.created_at)}</span>
          {isOut && <StatusTicks status={message.status} className="-mb-px" />}
        </div>
      </div>

      {message.status === 'failed' && onRetry && (
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
    prev.grouped === next.grouped
  );
});
