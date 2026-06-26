'use client';

import {
  Avatar,
  AvatarFallback,
  Badge,
  Button,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  Switch,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  cn,
} from '@reloop/ui';
import { ArrowLeft, PanelRight, Search, Trash2 } from 'lucide-react';
import { useToggleAutoReply } from '../hooks/use-toggle-auto-reply';
import { useConversationsContext } from '../lib/context';
import { contactDisplayName, contactInitials } from '../lib/initials';
import type { Conversation } from '../types';

interface ThreadHeaderProps {
  conversation: Conversation;
  onBack?: () => void;
  /** Toggle the lead detail panel (right rail on desktop, sheet on mobile). */
  onToggleDetail?: () => void;
  /** Whether the detail panel is currently open (for the toggle's active state). */
  detailActive?: boolean;
  /** Called after the user confirms deletion. */
  onDelete?: () => void;
  /** Whether a delete action is in progress. */
  isDeleting?: boolean;
}

export function ThreadHeader({
  conversation,
  onBack,
  onToggleDetail,
  detailActive,
  onDelete,
  isDeleting,
}: ThreadHeaderProps) {
  const { merchantAutoReplyEnabled } = useConversationsContext();
  const toggle = useToggleAutoReply();

  const resolved = conversation.status !== 'active';

  const phone = conversation.wa_contact_phone;
  const name = (conversation.meta?.['contact_name'] as string | undefined) ?? null;
  const display = contactDisplayName(name, phone);
  const initials = contactInitials(name, phone);

  // Effective auto-reply = merchant master AND per-thread.
  const merchantOff = merchantAutoReplyEnabled === false;
  const effective = !merchantOff && conversation.auto_reply;
  const statusLine = merchantOff
    ? 'Risposta manuale (account)'
    : effective
      ? 'Auto-risposta attiva'
      : 'Risposta manuale';

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
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card px-3 sm:px-4">
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
      <Avatar className="h-10 w-10">
        <AvatarFallback className="text-[12px] font-semibold">{initials}</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-semibold leading-tight">{display}</span>
          <Badge
            variant={resolved ? 'secondary' : 'success'}
            className="hidden shrink-0 sm:inline-flex"
          >
            {resolved ? 'Risolta' : 'Attiva'}
          </Badge>
        </div>
        <span className="truncate text-[11px] text-muted-foreground">
          {phone && name ? `${phone} · ${statusLine}` : statusLine}
        </span>
      </div>

      <div className="hidden items-center gap-2 sm:flex">
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

      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8 text-muted-foreground"
        aria-label="Cerca nella chat"
        disabled
      >
        <Search className="h-4 w-4" />
      </Button>

      {onDelete && (
        <Dialog>
          <Tooltip>
            <TooltipTrigger asChild>
              <DialogTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 text-muted-foreground hover:text-destructive"
                  aria-label="Elimina conversazione"
                  disabled={isDeleting}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </DialogTrigger>
            </TooltipTrigger>
            <TooltipContent side="bottom">Elimina conversazione</TooltipContent>
          </Tooltip>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Eliminare la conversazione?</DialogTitle>
              <DialogDescription>
                Tutti i messaggi con{' '}
                <span className="font-medium">{display}</span> verranno eliminati
                definitivamente. L&apos;operazione non è reversibile.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline">Annulla</Button>
              </DialogClose>
              <DialogClose asChild>
                <Button variant="destructive" onClick={onDelete} disabled={isDeleting}>
                  {isDeleting ? 'Eliminazione…' : 'Elimina'}
                </Button>
              </DialogClose>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {onToggleDetail && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className={cn(
                'h-8 w-8',
                detailActive ? 'bg-accent text-foreground' : 'text-muted-foreground',
              )}
              onClick={onToggleDetail}
              aria-label="Mostra dettagli contatto"
              aria-pressed={detailActive}
            >
              <PanelRight className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Dettagli contatto</TooltipContent>
        </Tooltip>
      )}
    </header>
  );
}
