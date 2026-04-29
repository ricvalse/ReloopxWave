'use client';

import { Button, cn, Textarea, Tooltip, TooltipContent, TooltipTrigger } from '@reloop/ui';
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
      <div className="border-t border-border bg-card-elevated px-4 py-3 text-center text-xs text-muted-foreground">
        {disabledReason}
      </div>
    );
  }

  return (
    <div className="border-t border-border bg-card-elevated">
      <div className="mx-auto flex max-w-4xl items-end gap-2 px-4 py-3">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-9 w-9 shrink-0 text-muted-foreground"
              disabled
            >
              <Paperclip className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">Allegati — disponibile a breve</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-9 w-9 shrink-0 text-muted-foreground"
              disabled
            >
              <Smile className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">Emoji — disponibile a breve</TooltipContent>
        </Tooltip>

        <Textarea
          ref={textareaRef}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Scrivi un messaggio…"
          className={cn(
            'flex-1 resize-none border-border bg-background px-3 py-2 text-sm leading-5',
            'min-h-9 max-h-40',
          )}
          disabled={disabled || sendMutation.isPending}
        />

        <Button
          onClick={submit}
          size="icon"
          disabled={!canSend}
          className="h-9 w-9 shrink-0"
          aria-label="Invia"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
