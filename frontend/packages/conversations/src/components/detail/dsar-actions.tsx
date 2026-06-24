'use client';

import {
  Button,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@reloop/ui';
import { AlertCircle, Check, Download, Loader2, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { downloadJson, useEraseLead, useExportLead } from '../../hooks/use-dsar';

interface DsarActionsProps {
  /** Lead linked to the conversation; null when no lead exists yet. */
  leadId: string | null | undefined;
  /** Display name used to label the downloaded file. */
  contactLabel: string;
}

/**
 * GDPR data-subject actions on the lead: "Esporta dati" (right of access — downloads
 * the lead + conversations + messages as JSON) and "Cancella dati" (right to erasure —
 * deletes conversations and strips PII, behind a confirmation). Both call the backend
 * `dsar` router; RLS scopes them to the merchant. Hidden when the conversation has no
 * lead yet (nothing to export/erase).
 */
export function DsarActions({ leadId, contactLabel }: DsarActionsProps) {
  const exportLead = useExportLead();
  const eraseLead = useEraseLead();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [exported, setExported] = useState(false);

  if (!leadId) return null;

  const onExport = () => {
    setExported(false);
    exportLead.mutate(leadId, {
      onSuccess: (payload) => {
        const safe = contactLabel.replace(/[^a-z0-9]+/gi, '-').toLowerCase() || 'lead';
        downloadJson(`dati-${safe}-${leadId}.json`, payload);
        setExported(true);
      },
    });
  };

  const onErase = () => {
    eraseLead.mutate(leadId, {
      onSuccess: () => setConfirmOpen(false),
    });
  };

  return (
    <div className="space-y-2">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        Privacy (GDPR)
      </p>
      <div className="flex flex-col gap-2">
        <Button
          variant="outline"
          size="sm"
          className="justify-start"
          onClick={onExport}
          disabled={exportLead.isPending}
        >
          {exportLead.isPending ? (
            <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
          ) : exported ? (
            <Check className="mr-2 h-3.5 w-3.5 text-success" />
          ) : (
            <Download className="mr-2 h-3.5 w-3.5" />
          )}
          Esporta dati
        </Button>
        {exportLead.isError && (
          <p className="flex items-center gap-1 text-[11px] text-destructive">
            <AlertCircle className="h-3 w-3" />
            Esportazione non riuscita — riprova
          </p>
        )}

        <Button
          variant="outline"
          size="sm"
          className="justify-start text-destructive hover:text-destructive"
          onClick={() => setConfirmOpen(true)}
        >
          <Trash2 className="mr-2 h-3.5 w-3.5" />
          Cancella dati
        </Button>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancellare i dati del contatto?</DialogTitle>
            <DialogDescription>
              Verranno eliminate tutte le conversazioni e i messaggi di{' '}
              <span className="font-medium text-foreground">{contactLabel}</span> e rimossi i dati
              personali del lead (nome, email, riferimento CRM). L&apos;operazione è irreversibile.
            </DialogDescription>
          </DialogHeader>
          {eraseLead.isError && (
            <p className="flex items-center gap-1 text-[11px] text-destructive">
              <AlertCircle className="h-3 w-3" />
              Cancellazione non riuscita — riprova
            </p>
          )}
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" size="sm" disabled={eraseLead.isPending}>
                Annulla
              </Button>
            </DialogClose>
            <Button
              variant="default"
              size="sm"
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={onErase}
              disabled={eraseLead.isPending}
            >
              {eraseLead.isPending ? (
                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="mr-2 h-3.5 w-3.5" />
              )}
              Cancella definitivamente
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
