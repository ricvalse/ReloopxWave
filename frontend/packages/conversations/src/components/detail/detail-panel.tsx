'use client';

import { Avatar, AvatarFallback, Button, ScrollArea, Skeleton } from '@reloop/ui';
import { X } from 'lucide-react';
import { useLeadDetail } from '../../hooks/use-lead-detail';
import { contactDisplayName, contactInitials } from '../../lib/initials';
import type { Conversation } from '../../types';
import { ContactInfo } from './contact-info';
import { LeadScore } from './lead-score';
import { NotesEditor } from './notes-editor';
import { ObjectionsList } from './objections-list';

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
