'use client';

import { useEffect, useState } from 'react';
import { Button } from '@reloop/ui';
import { Bot, UserRound } from 'lucide-react';
import { useAiPause, useAiResume } from '../hooks/use-ai-pause';
import type { Conversation } from '../types';

/** Human-readable remaining time until `iso`, or null if it's already past. */
function remaining(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return null;
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.ceil(hours / 24)}g`;
}

/**
 * Shown above the composer when the bot is off this thread (escalated, taken
 * over, or soft-paused). Surfaces the AI's handoff brief, a live countdown when
 * the pause auto-resumes, and one-tap "Riattiva AI" / "Pausa 2h" controls.
 * Renders nothing while the bot is active.
 */
export function AiHandoffBanner({ conversation }: { conversation: Conversation }) {
  const pause = useAiPause();
  const resume = useAiResume();
  // Re-render every 30s so the countdown ticks down without a server round-trip.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 30000);
    return () => clearInterval(id);
  }, []);

  const paused = conversation.auto_reply === false;
  const countdown = remaining(conversation.ai_disabled_until);
  if (!paused && !countdown) return null;

  const reason = conversation.handoff_reason;
  const label = countdown
    ? `AI in pausa · riprende tra ${countdown}`
    : reason === 'manual_reply'
      ? 'Stai gestendo tu questa chat'
      : 'AI in pausa — rispondi manualmente';

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
      <span className="flex items-center gap-1.5 font-medium">
        <UserRound className="h-3.5 w-3.5" />
        {label}
      </span>
      {conversation.handoff_summary ? (
        <span className="min-w-0 flex-1 truncate text-amber-800/90 dark:text-amber-200/80">
          {conversation.handoff_summary}
        </span>
      ) : (
        <span className="flex-1" />
      )}
      <div className="flex items-center gap-1.5">
        {!countdown ? (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-[11px] text-amber-900 hover:bg-amber-100 dark:text-amber-200"
            disabled={pause.isPending}
            onClick={() => pause.mutate({ conversationId: conversation.id, hours: 2 })}
          >
            Pausa 2h
          </Button>
        ) : null}
        <Button
          variant="outline"
          size="sm"
          className="h-6 gap-1 border-amber-300 px-2 text-[11px] text-amber-900 hover:bg-amber-100 dark:border-amber-800 dark:text-amber-200"
          disabled={resume.isPending}
          onClick={() => resume.mutate({ conversationId: conversation.id })}
        >
          <Bot className="h-3 w-3" />
          Riattiva AI
        </Button>
      </div>
    </div>
  );
}
