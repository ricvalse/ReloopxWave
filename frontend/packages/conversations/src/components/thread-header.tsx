'use client';

import {
  Avatar,
  AvatarFallback,
  Badge,
  Button,
  Switch,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@reloop/ui';
import { ArrowLeft, Bot, MoreHorizontal, Phone } from 'lucide-react';
import { useToggleAutoReply } from '../hooks/use-toggle-auto-reply';
import { useConversationsContext } from '../lib/context';
import { contactDisplayName, contactInitials } from '../lib/initials';
import type { Conversation } from '../types';

interface ThreadHeaderProps {
  conversation: Conversation;
  onBack?: () => void;
}

export function ThreadHeader({ conversation, onBack }: ThreadHeaderProps) {
  const { merchantAutoReplyEnabled } = useConversationsContext();
  const toggle = useToggleAutoReply();

  const phone = conversation.wa_contact_phone;
  const name = (conversation.meta?.['contact_name'] as string | undefined) ?? null;
  const display = contactDisplayName(name, phone);
  const initials = contactInitials(name, phone);

  // Effective auto-reply = merchant master AND per-thread. The Switch shows
  // the AND result so the user sees what the bot will actually do; flipping
  // it only changes the per-thread side.
  const merchantOff = merchantAutoReplyEnabled === false;
  const effective = !merchantOff && conversation.auto_reply;

  const switchEl = (
    <Switch
      checked={effective}
      disabled={merchantOff || toggle.isPending}
      onCheckedChange={(v) =>
        toggle.mutate({ conversationId: conversation.id, autoReply: v })
      }
      aria-label="Risposta automatica del bot"
    />
  );

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background/80 px-3 backdrop-blur-sm sm:px-4">
      {onBack && (
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 md:hidden"
          onClick={onBack}
          aria-label="Indietro"
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
      )}
      <Avatar className="h-9 w-9">
        <AvatarFallback className="text-[11px] font-semibold">{initials}</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-medium leading-tight">{display}</span>
        {phone && name && (
          <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <Phone className="h-3 w-3" />
            {phone}
          </span>
        )}
      </div>

      <div className="hidden items-center gap-2 sm:flex">
        <Bot className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-[11px] font-medium text-muted-foreground">Bot</span>
        {merchantOff ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span>{switchEl}</span>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-[220px] text-center">
              La risposta automatica è disattivata a livello account. Riattivala
              da Configurazione bot.
            </TooltipContent>
          </Tooltip>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <span>{switchEl}</span>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {effective
                ? 'Il bot risponde automaticamente. Disattiva per gestire questa chat manualmente.'
                : 'Il bot è in pausa su questa chat. I messaggi in arrivo aspettano una tua risposta.'}
            </TooltipContent>
          </Tooltip>
        )}
      </div>

      <Badge
        variant="outline"
        className="hidden text-[10px] uppercase tracking-wider lg:inline-flex"
      >
        WhatsApp
      </Badge>
      <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Altro">
        <MoreHorizontal className="h-4 w-4" />
      </Button>
    </header>
  );
}
