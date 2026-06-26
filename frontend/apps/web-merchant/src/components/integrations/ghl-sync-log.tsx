'use client';

import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Skeleton,
} from '@reloop/ui';
import { AlertCircle, CheckCircle2, Clock } from 'lucide-react';
import { useGhlSyncLog, type GhlSyncEntry } from '@/hooks/use-ghl-sync-log';

const OPERATION_LABEL: Record<string, string> = {
  'contact.upserted': 'Contatto sincronizzato',
  'opportunity.created': 'Opportunità creata',
  'opportunity.moved': 'Opportunità spostata',
  'note.added': 'Nota aggiunta',
  'booking.created': 'Appuntamento creato',
  'booking.rescheduled': 'Appuntamento spostato',
  'booking.cancelled': 'Appuntamento cancellato',
};

const ENTITY_LABEL: Record<string, string> = {
  contact: 'Contatto',
  opportunity: 'Opportunità',
  appointment: 'Appuntamento',
  note: 'Nota',
};

function relativeTime(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'adesso';
  if (mins < 60) return `${mins} min fa`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h fa`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} g fa`;
  return new Date(iso).toLocaleDateString('it-IT', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function StatusIcon({ status }: { status: string }) {
  if (status === 'success')
    return <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />;
  if (status === 'error')
    return <AlertCircle className="h-4 w-4 shrink-0 text-red-500" />;
  return <Clock className="h-4 w-4 shrink-0 text-muted-foreground" />;
}

function SyncRow({ entry }: { entry: GhlSyncEntry }) {
  const label = OPERATION_LABEL[entry.operation] ?? entry.operation;
  const entityLabel = entry.ghl_entity_type ? ENTITY_LABEL[entry.ghl_entity_type] : null;

  return (
    <div className="flex items-start gap-3 py-2.5">
      <StatusIcon status={entry.status} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium">{label}</span>
          {entityLabel && (
            <Badge variant="outline" className="text-[10px] px-1.5 py-0">
              {entityLabel}
            </Badge>
          )}
          {entry.status === 'error' && (
            <Badge variant="destructive" className="text-[10px] px-1.5 py-0">
              errore
            </Badge>
          )}
        </div>
        {entry.ghl_entity_id && (
          <p className="text-[11px] font-mono text-muted-foreground truncate">
            ID: {entry.ghl_entity_id}
          </p>
        )}
        {entry.error_detail && (
          <p className="text-[11px] text-red-500 truncate">{entry.error_detail}</p>
        )}
      </div>
      <span className="shrink-0 text-[11px] text-muted-foreground">{relativeTime(entry.occurred_at)}</span>
    </div>
  );
}

export function GhlSyncLog() {
  const { data, isLoading, isError } = useGhlSyncLog({ sinceDays: 30 });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Log sincronizzazioni GHL</CardTitle>
        <p className="text-sm text-muted-foreground">
          Ogni operazione inviata a GoHighLevel negli ultimi 30 giorni.
        </p>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading && !data && (
          <div className="space-y-2 px-6 pb-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        )}
        {isError && (
          <p className="px-6 pb-4 text-sm text-destructive">
            Errore caricamento log sincronizzazioni.
          </p>
        )}
        {data && data.length === 0 && (
          <p className="px-6 pb-4 text-sm text-muted-foreground">
            Nessuna sincronizzazione negli ultimi 30 giorni.
          </p>
        )}
        {data && data.length > 0 && (
          <div className="divide-y divide-border px-6">
            {data.map((entry) => (
              <SyncRow key={entry.id} entry={entry} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
