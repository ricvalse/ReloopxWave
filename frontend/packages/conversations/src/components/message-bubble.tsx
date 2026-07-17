'use client';

import { cn } from '@reloop/ui';
import { memo } from 'react';
import { formatBubbleTime } from '../lib/time';
import { isAutomationSender, type Message } from '../types';
import { MessageMedia } from './message-media';
import { StatusTicks } from './status-ticks';

// The synthesized placeholders the backend writes as `content` for uncaptioned
// media (mirror of `webhooks.py:_MEDIA_PLACEHOLDER`). When the real attachment
// renders, the placeholder text is redundant, so we suppress it.
const MEDIA_PLACEHOLDERS = new Set([
  "[Il cliente ha inviato un'immagine]",
  '[Il cliente ha inviato un messaggio vocale]',
  '[Il cliente ha inviato un video]',
  '[Il cliente ha inviato un documento]',
  '[Il cliente ha inviato uno sticker]',
]);

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
  const isFailed = message.status === 'failed';
  const isFromPhone = message.meta?.sender_type === 'phone';
  const isFromAutomation = isAutomationSender(message.meta?.sender_type);
  const originLabel = isFromPhone ? 'Da telefono' : isFromAutomation ? 'Automazione' : null;
  const showTicks = isOut;
  const spacer = showTicks ? META_SPACER_WIDTH : META_SPACER_WIDTH_NO_TICKS;

  // WhatsApp palette: green bubbles outbound, white bubbles inbound.
  // Meta (timestamp/ticks/"Da telefono") inverts so it stays readable on green.
  const metaColor = isOut ? 'text-white/75' : 'text-black/45 dark:text-white/55';

  const media = message.meta?.media ?? null;
  // Hide the synthesized placeholder text once the real attachment is showing;
  // a real caption (anything not in the placeholder set) is always kept.
  const showText = !(media && MEDIA_PLACEHOLDERS.has(message.content));

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
          // Width caps:
          //  - mobile: up to 85% of the row width
          //  - ≥sm: 72% of row width, hard-capped at 560px so long messages stay
          //    readable when the chat panel is very wide.
          'relative max-w-[85%] rounded-2xl px-2.5 pb-1.5 pt-1.5 text-sm shadow-[0_1px_0.5px_rgba(0,0,0,0.13)] sm:max-w-[min(72%,560px)]',
          isOut
            ? cn(
                // Outbound: WhatsApp green, white text. Tail only on group leader.
                'bg-[#25d366] text-white dark:bg-[#005c4b]',
                !grouped && 'rounded-tr-[4px]',
              )
            : cn(
                // Inbound: white card. Tail only on group leader.
                'bg-white text-gray-900 dark:bg-[#202c33] dark:text-gray-100',
                !grouped && 'rounded-tl-[4px]',
              ),
          isFailed && 'opacity-80',
        )}
      >
        {originLabel && (
          <span
            className={cn(
              'mb-0.5 block text-[10px] font-medium uppercase tracking-wide',
              metaColor,
            )}
          >
            {originLabel}
          </span>
        )}
        {media && <MessageMedia message={message} metaColor={metaColor} />}
        <span className="whitespace-pre-wrap break-words leading-relaxed">
          {showText && message.content}
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
            metaColor,
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
    // The two-phase media write lands `storage_path` (and maybe `error` /
    // `transcription`) via a Realtime UPDATE — without these the bubble would
    // never re-render to swap the "Caricamento…" placeholder for the image.
    prev.message.meta?.media?.storage_path === next.message.meta?.media?.storage_path &&
    prev.message.meta?.media?.error === next.message.meta?.media?.error &&
    prev.message.meta?.media?.transcription === next.message.meta?.media?.transcription &&
    prev.grouped === next.grouped
  );
});
