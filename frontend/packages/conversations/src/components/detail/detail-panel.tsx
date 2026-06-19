'use client';

import { Avatar, AvatarFallback, Button, ScrollArea, Skeleton } from '@reloop/ui';
import { UserRound, X } from 'lucide-react';
import { useLeadDetail } from '../../hooks/use-lead-detail';
import { contactDisplayName, contactInitials } from '../../lib/initials';
import type { Conversation } from '../../types';
import { ContactInfo } from './contact-info';
import { LeadScore } from './lead-score';
import { NotesEditor } from './notes-editor';
import { ObjectionsList } from './objections-list';

const HANDOFF_REASON_LABEL: Record<string, string> = {
  manual_reply: 'Presa in carico manuale',
  video_message: 'Media non gestibile (video)',
  document_message: 'Media non gestibile (documento)',
  angry: 'Cliente insoddisfatto',
};

function relativeTime(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'adesso';
  if (mins < 60) return `${mins} min fa`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h fa`;
  return `${Math.floor(hours / 24)} g fa`;
}

/** Escalation / handoff context for the operator. Hidden while the bot is on. */
function HandoffSection({ conversation }: { conversation: Conversation }) {
  if (conversation.auto_reply !== false && !conversation.handoff_at) return null;
  const reason = conversation.handoff_reason;
  const label = (reason && HANDOFF_REASON_LABEL[reason]) || 'Passata a un operatore';
  return (
    <section className="border-t border-border bg-amber-50/60 px-4 py-4 dark:bg-amber-950/20">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-amber-900 dark:text-amber-200">
        <UserRound className="h-3.5 w-3.5" />
        {label}
        {conversation.handoff_at ? (
          <span className="font-normal text-amber-800/70 dark:text-amber-200/60">
            · {relativeTime(conversation.handoff_at)}
          </span>
        ) : null}
      </div>
      {conversation.handoff_summary ? (
        <p className="text-xs leading-relaxed text-amber-900/90 dark:text-amber-100/80">
          {conversation.handoff_summary}
        </p>
      ) : null}
      {conversation.assigned_to ? (
        <p className="mt-1 text-[11px] text-amber-800/70 dark:text-amber-200/60">
          Assegnata a {conversation.assigned_to}
        </p>
      ) : null}
    </section>
  );
}

interface DetailPanelProps {
  conversation: Conversation;
  /** Collapses the right rail (desktop) or closes the sheet (mobile). */
  onClose: () => void;
  /** Hide the close button when rendered inside a sheet that owns its own. */
  hideClose?: boolean;
}

// Scope note: amalia's detail panel showed Shopify orders / COD status / customer
// LTV, and message media. Reloop is a GHL lead-gen product with no orders and no
// media columns on `messages` — that content is intentionally omitted here. The
// panel is lead-centric: score, sentiment, status, pipeline, contact, objections,
// notes. See the architecture doc's ConversationViewer.
export function DetailPanel({ conversation, onClose, hideClose }: DetailPanelProps) {
  const { data, isLoading } = useLeadDetail(conversation.id, conversation.lead_id);

  const name = (conversation.meta?.['contact_name'] as string | undefined) ?? null;
  const display = contactDisplayName(name, conversation.wa_contact_phone);
  const initials = contactInitials(name, conversation.wa_contact_phone);

  const lead = data?.lead ?? null;
  const objections = data?.objections ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col bg-card">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <Avatar className="h-9 w-9">
          <AvatarFallback className="text-[11px] font-semibold">{initials}</AvatarFallback>
        </Avatar>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold leading-tight">{display}</p>
          <p className="text-[11px] text-muted-foreground">Dettagli contatto</p>
        </div>
        {!hideClose && (
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-muted-foreground"
            onClick={onClose}
            aria-label="Chiudi dettagli"
          >
            <X className="h-4 w-4" />
          </Button>
        )}
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col">
          {isLoading && !data ? (
            <div className="space-y-3 p-4">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : (
            <>
              <HandoffSection conversation={conversation} />
              {lead && (
                <section className="px-4 py-4">
                  <LeadScore lead={lead} />
                </section>
              )}
              <section className="border-t border-border px-4 py-4">
                <ContactInfo conversation={conversation} lead={lead} />
              </section>
              <section className="border-t border-border px-4 py-4">
                <ObjectionsList objections={objections} />
              </section>
              <section className="border-t border-border px-4 py-4">
                <NotesEditor conversationId={conversation.id} note={data?.note ?? null} />
              </section>
            </>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
