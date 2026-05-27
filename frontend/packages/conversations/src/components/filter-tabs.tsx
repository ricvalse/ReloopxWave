'use client';

import { Tabs, TabsList, TabsTrigger, cn } from '@reloop/ui';
import { useMemo } from 'react';
import type { Conversation, InboxFilter } from '../types';

/**
 * Fold (`status` + `auto_reply`) into the agent-meaningful bucket a thread
 * belongs to. Exported so the workspace filters the list with the exact same
 * rule used to compute the tab counts.
 */
export function matchesInboxFilter(c: Conversation, filter: InboxFilter): boolean {
  switch (filter) {
    case 'all':
      return true;
    case 'active':
      return c.status === 'active';
    case 'needs_human':
      return c.status === 'active' && c.auto_reply === false;
    case 'resolved':
      return c.status !== 'active';
  }
}

const TABS: { value: InboxFilter; label: string }[] = [
  { value: 'all', label: 'Tutte' },
  { value: 'active', label: 'Attive' },
  { value: 'needs_human', label: 'Da gestire' },
  { value: 'resolved', label: 'Risolte' },
];

interface FilterTabsProps {
  /** Search-filtered conversations — counts are computed from these. */
  conversations: Conversation[];
  value: InboxFilter;
  onChange: (filter: InboxFilter) => void;
}

export function FilterTabs({ conversations, value, onChange }: FilterTabsProps) {
  const counts = useMemo(() => {
    const c: Record<InboxFilter, number> = { all: 0, active: 0, needs_human: 0, resolved: 0 };
    for (const conv of conversations) {
      c.all += 1;
      if (conv.status === 'active') c.active += 1;
      if (conv.status === 'active' && conv.auto_reply === false) c.needs_human += 1;
      if (conv.status !== 'active') c.resolved += 1;
    }
    return c;
  }, [conversations]);

  return (
    <Tabs value={value} onValueChange={(v) => onChange(v as InboxFilter)}>
      <TabsList className="flex h-8 w-full justify-between gap-0.5 overflow-x-auto p-0.5">
        {TABS.map((t) => {
          const count = counts[t.value];
          const highlight = t.value === 'needs_human' && count > 0;
          return (
            <TabsTrigger
              key={t.value}
              value={t.value}
              className="min-w-fit flex-1 gap-1 px-2 py-1 text-[11px]"
            >
              <span className="truncate">{t.label}</span>
              {count > 0 && (
                <span
                  className={cn(
                    'rounded-full px-1 text-[9px] font-semibold leading-4 tabular-nums',
                    highlight
                      ? 'bg-warning/20 text-warning'
                      : 'bg-muted-foreground/15 text-muted-foreground',
                    'data-[state=active]:bg-primary/15 data-[state=active]:text-primary',
                  )}
                >
                  {count > 99 ? '99+' : count}
                </span>
              )}
            </TabsTrigger>
          );
        })}
      </TabsList>
    </Tabs>
  );
}
