'use client';

import { Button, cn, Tooltip, TooltipContent, TooltipTrigger } from '@reloop/ui';
import { Paperclip, Send, Smile } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useSendMessage } from '../hooks/use-send-message';

interface ComposerProps {
  conversationId: string;
  disabled?: boolean;
  disabledReason?: string;
}

const MAX_ROWS = 8;
const LINE_HEIGHT_PX = 20;

function newClientMessageId(): string {
  // crypto.randomUUID is available in modern browsers + Node ≥19
  return crypto.randomUUID();
}

export function Composer({ conversationId, disabled, disabledReason }: ComposerProps) {
  const [text, setText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const sendMutation = useSendMessage();

  // Auto-grow up to MAX_ROWS lines
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    const max = LINE_HEIGHT_PX * MAX_ROWS + 16;
    ta.style.height = Math.min(ta.scrollHeight, max) + 'px';
  }, [text]);

  // Reset text on conversation change
  useEffect(() => {
    setText('');
  }, [conversationId]);

  const trimmed = text.trim();
  const canSend = !disabled && trimmed.length > 0 && !sendMutation.isPending;

  function submit() {
    if (!canSend) return;
    const clientMessageId = newClientMessageId();
    sendMutation.mutate({
      conversationId,
      text: trimmed,
      clientMessageId,
    });
    setText('');
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  if (disabled && disabledReason) {
    return (
      <div className="border-t border-border bg-card px-4 py-3 text-center text-xs text-muted-foreground">
        {disabledReason}
      </div>
    );
  }

  return (
    <div className="border-t border-border bg-card">
      <div className="mx-auto flex max-w-3xl items-end gap-2 px-3 py-2 sm:px-4 sm:py-3">
        <div
          className={cn(
            'flex flex-1 items-end gap-1 rounded-3xl border border-border bg-background pl-1 pr-1.5',
            'transition-colors focus-within:border-ring',
          )}
        >
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-9 w-9 shrink-0 rounded-full text-muted-foreground"
                disabled
                aria-label="Emoji"
              >
                <Smile className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">Emoji — disponibile a breve</TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-9 w-9 shrink-0 rounded-full text-muted-foreground"
                disabled
                aria-label="Allegati"
              >
                <Paperclip className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">Allegati — disponibile a breve</TooltipContent>
          </Tooltip>

          <textarea
            ref={textareaRef}
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Scrivi un messaggio…"
            className={cn(
              'flex-1 resize-none bg-transparent px-1 py-2 text-sm leading-5 outline-none',
              'placeholder:text-muted-foreground',
              'min-h-9 max-h-40',
            )}
            disabled={disabled || sendMutation.isPending}
          />
        </div>

        <Button
          onClick={submit}
          size="icon"
          disabled={!canSend}
          className="h-10 w-10 shrink-0 rounded-full"
          aria-label="Invia"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
