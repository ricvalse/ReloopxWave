'use client';

import { Badge } from '@reloop/ui';
import { GitBranch, Mail, Phone } from 'lucide-react';
import type { Conversation, Lead } from '../../types';

/** Tags aren't a DB column — read defensively from lead.meta.tags. */
function readTags(lead: Lead | null): string[] {
  const raw = lead?.meta?.['tags'];
  if (Array.isArray(raw) && raw.every((t) => typeof t === 'string')) return raw as string[];
  return [];
}

interface ContactInfoProps {
  conversation: Conversation;
  lead: Lead | null;
}

export function ContactInfo({ conversation, lead }: ContactInfoProps) {
  const phone = lead?.phone ?? conversation.wa_contact_phone ?? null;
  const email = lead?.email ?? null;
  const status = lead?.status ?? null;
  const stage = lead?.pipeline_stage_id ?? null;
  const tags = readTags(lead);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Contatto
        </p>
        {status && (
          <Badge variant="outline" className="capitalize">
            {status}
          </Badge>
        )}
      </div>

      <dl className="space-y-2 text-[13px]">
        <div className="flex items-center gap-2.5">
          <Phone className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <dd className="truncate tabular-nums">{phone ? `+${phone.replace(/^\+/, '')}` : '—'}</dd>
        </div>
        <div className="flex items-center gap-2.5">
          <Mail className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <dd className="truncate">{email ?? <span className="text-muted-foreground">—</span>}</dd>
        </div>
        {stage && (
          <div className="flex items-center gap-2.5">
            <GitBranch className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <dd className="truncate text-muted-foreground" title={stage}>
              {stage}
            </dd>
          </div>
        )}
      </dl>

      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-0.5">
          {tags.map((tag) => (
            <Badge key={tag} variant="secondary" className="font-normal">
              {tag}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
