'use client';

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
} from '@reloop/ui';
import { useMemo, useState } from 'react';
import { useApprovedTemplates, type WhatsAppTemplate } from '../hooks/use-templates';
import type { TemplateSendPayload } from '../hooks/use-send-message';

interface TemplatePickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fired with the rendered preview text + template payload to send. */
  onSend: (args: { preview: string; template: TemplateSendPayload }) => void;
  sending?: boolean;
}

/** Render `{{1}}`-style placeholders with the agent-supplied values for preview. */
function renderBody(body: string, values: string[]): string {
  return body.replace(/\{\{(\d+)\}\}/g, (_m, n: string) => {
    const idx = Number(n) - 1;
    const v = values[idx];
    return v && v.trim() ? v : `{{${n}}}`;
  });
}

export function TemplatePicker({ open, onOpenChange, onSend, sending }: TemplatePickerProps) {
  const { data: templates, isLoading, isError, error } = useApprovedTemplates(open);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});

  const selected: WhatsAppTemplate | undefined = useMemo(
    () => templates?.find((t) => t.id === selectedId),
    [templates, selectedId],
  );

  function select(t: WhatsAppTemplate) {
    setSelectedId(t.id);
    setValues({});
  }

  function reset() {
    setSelectedId(null);
    setValues({});
  }

  const orderedValues = selected ? selected.variables.map((_, i) => values[String(i)] ?? '') : [];
  const allFilled =
    selected != null && orderedValues.every((v) => v.trim().length > 0);

  function submit() {
    if (!selected || !allFilled) return;
    onSend({
      preview: renderBody(selected.body, orderedValues),
      template: {
        name: selected.name,
        language: selected.language,
        variables: orderedValues,
      },
    });
    reset();
    onOpenChange(false);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Invia un template approvato</DialogTitle>
          <DialogDescription>
            La finestra di 24h è chiusa: solo un template approvato può raggiungere il cliente.
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Caricamento template…</p>
        ) : isError ? (
          <p className="py-6 text-center text-sm text-destructive">
            {error instanceof Error ? error.message : 'Errore nel caricamento dei template.'}
          </p>
        ) : !templates || templates.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Nessun template approvato disponibile. Creane uno nella sezione Template.
          </p>
        ) : !selected ? (
          <div className="max-h-72 space-y-1 overflow-y-auto py-1">
            {templates.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => select(t)}
                className="flex w-full flex-col items-start gap-0.5 rounded-lg border border-border px-3 py-2 text-left transition-colors hover:border-ring hover:bg-accent"
              >
                <span className="text-sm font-medium">{t.name}</span>
                <span className="line-clamp-2 text-xs text-muted-foreground">{t.body}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="space-y-3 py-1">
            <div className="rounded-lg bg-muted/50 px-3 py-2 text-sm">
              {renderBody(selected.body, orderedValues)}
            </div>
            {selected.variables.length > 0 ? (
              <div className="space-y-2">
                {selected.variables.map((name, i) => (
                  <div key={i} className="space-y-1">
                    <Label htmlFor={`tpl-var-${i}`} className="text-xs">
                      {name || `Variabile ${i + 1}`}
                    </Label>
                    <Input
                      id={`tpl-var-${i}`}
                      value={values[String(i)] ?? ''}
                      onChange={(e) =>
                        setValues((prev) => ({ ...prev, [String(i)]: e.target.value }))
                      }
                      placeholder={`Valore per {{${i + 1}}}`}
                    />
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        )}

        <DialogFooter>
          {selected ? (
            <>
              <Button variant="ghost" onClick={reset} disabled={sending}>
                Indietro
              </Button>
              <Button onClick={submit} disabled={!allFilled || sending}>
                Invia template
              </Button>
            </>
          ) : (
            <Button variant="ghost" onClick={() => onOpenChange(false)}>
              Annulla
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
