'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Textarea,
} from '@reloop/ui';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import {
  MAX_BODY_LEN,
  MAX_BUTTONS_TOTAL,
  MAX_FOOTER_LEN,
  MAX_HEADER_TEXT_LEN,
  TEMPLATE_GUIDELINES,
  extractVariables,
  lintTemplate,
  type LintIssue,
  type TemplateButtonInput,
} from '@/lib/whatsapp-template-lint';
import { WhatsAppTemplatePreview } from './whatsapp-template-preview';

type Template = components['schemas']['WhatsAppTemplateOut'];

const PURPOSES = [
  { value: 'reactivation', label: 'Riattivazione' },
  { value: 'no_answer_1', label: 'No-answer #1' },
  { value: 'no_answer_2', label: 'No-answer #2' },
  { value: 'booking_reminder', label: 'Promemoria appuntamento' },
  { value: 'first_contact', label: 'Primo contatto' },
  { value: 'custom', label: 'Personalizzato' },
];

const CATEGORIES = ['UTILITY', 'MARKETING', 'AUTHENTICATION'];

const CATEGORY_HINTS: Record<string, string> = {
  UTILITY: 'Notifiche transazionali: conferme ordine, promemoria appuntamento, aggiornamenti.',
  MARKETING: 'Promozioni e offerte. Approvazione più severa: serve contenuto chiaramente commerciale.',
  AUTHENTICATION: 'Solo per codici OTP/verifica. Formato rigido imposto da Meta.',
};

const BUTTON_TYPES = [
  { value: 'QUICK_REPLY', label: 'Risposta rapida' },
  { value: 'URL', label: 'Apri link (URL)' },
  { value: 'PHONE_NUMBER', label: 'Chiama numero' },
];

const STATUS_VARIANT: Record<string, 'success' | 'destructive' | 'warning' | 'secondary'> = {
  approved: 'success',
  rejected: 'destructive',
  pending_approval: 'warning',
  draft: 'secondary',
};

const STATUS_LABEL: Record<string, string> = {
  approved: 'Approvato',
  rejected: 'Rifiutato',
  pending_approval: 'In approvazione',
  draft: 'Bozza',
};

export function TemplatesPanel() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Template | null>(null);

  const templates = useQuery({
    queryKey: ['whatsapp-templates'],
    queryFn: async (): Promise<Template[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/whatsapp-templates');
      if (error) throw new Error(apiErrorMessage(error));
      return data as Template[];
    },
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['whatsapp-templates'] });

  const syncTemplate = useMutation({
    mutationFn: async (id: string) => {
      const api = getApiClient();
      const { error } = await api.POST('/whatsapp-templates/{template_id}/sync', {
        params: { path: { template_id: id } },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: invalidate,
  });

  const submitTemplate = useMutation({
    mutationFn: async (id: string) => {
      const api = getApiClient();
      const { error } = await api.POST('/whatsapp-templates/{template_id}/submit', {
        params: { path: { template_id: id } },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: invalidate,
  });

  const deleteTemplate = useMutation({
    mutationFn: async (id: string) => {
      const api = getApiClient();
      const { error } = await api.DELETE('/whatsapp-templates/{template_id}', {
        params: { path: { template_id: id } },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: invalidate,
  });

  if (templates.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Caricamento template…</div>;
  }
  if (templates.isError) {
    return (
      <div className="p-6 text-sm text-destructive">
        Errore: {templates.error instanceof Error ? templates.error.message : 'sconosciuto'}
      </div>
    );
  }

  const rows = templates.data ?? [];

  const openCreate = () => {
    setEditing(null);
    setShowForm((v) => !v);
  };
  const openEdit = (t: Template) => {
    setEditing(t);
    setShowForm(true);
  };
  const closeForm = () => {
    setShowForm(false);
    setEditing(null);
    void invalidate();
  };

  return (
    <div className="space-y-4 p-6">
      <div className="flex justify-end">
        <Button onClick={openCreate}>{showForm && !editing ? 'Annulla' : 'Nuovo template'}</Button>
      </div>

      {showForm ? (
        <TemplateForm key={editing?.id ?? 'new'} editing={editing} onDone={closeForm} />
      ) : null}

      {rows.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Nessun template. Creane uno per inviare messaggi fuori dalla finestra di 24h.
          </CardContent>
        </Card>
      ) : (
        rows.map((t) => (
          <Card key={t.id}>
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div>
                <CardTitle className="text-base">{t.name}</CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t.purpose} · {t.category} · {t.language}
                </p>
              </div>
              <Badge variant={STATUS_VARIANT[t.status] ?? 'secondary'}>
                {STATUS_LABEL[t.status] ?? t.status}
              </Badge>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="whitespace-pre-wrap text-sm">{t.body}</p>
              {t.rejection_reason ? (
                <p className="text-xs text-destructive">Motivo rifiuto: {t.rejection_reason}</p>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                {t.status === 'draft' || t.status === 'rejected' ? (
                  <>
                    <Button
                      size="sm"
                      onClick={() => submitTemplate.mutate(t.id)}
                      disabled={submitTemplate.isPending}
                    >
                      Invia per approvazione
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => openEdit(t)}>
                      Modifica
                    </Button>
                  </>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => syncTemplate.mutate(t.id)}
                    disabled={syncTemplate.isPending}
                  >
                    Sincronizza stato
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => deleteTemplate.mutate(t.id)}
                  disabled={deleteTemplate.isPending}
                >
                  Elimina
                </Button>
              </div>
            </CardContent>
          </Card>
        ))
      )}
    </div>
  );
}

function TemplateGuidelines() {
  return (
    <details className="rounded-md border border-input bg-muted/40 px-3 py-2 text-sm">
      <summary className="cursor-pointer select-none font-medium">
        Linee guida per l’approvazione — evita il rifiuto «Invalid Format»
      </summary>
      <div className="mt-3 space-y-3">
        {TEMPLATE_GUIDELINES.map((section) => (
          <div key={section.title} className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {section.title}
            </p>
            <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
              {section.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </details>
  );
}

function FieldIssues({ issues, field }: { issues: LintIssue[]; field: LintIssue['field'] }) {
  const list = issues.filter((i) => i.field === field);
  if (list.length === 0) return null;
  return (
    <ul className="space-y-1 pt-0.5">
      {list.map((i) => (
        <li
          key={i.code}
          className={
            i.level === 'error'
              ? 'text-xs text-destructive'
              : 'text-xs text-amber-600 dark:text-amber-500'
          }
        >
          {i.level === 'error' ? '⛔ ' : '⚠️ '}
          {i.message}
        </li>
      ))}
    </ul>
  );
}

function TemplateForm({ editing, onDone }: { editing: Template | null; onDone: () => void }) {
  const [purpose, setPurpose] = useState(editing?.purpose ?? 'reactivation');
  const [category, setCategory] = useState(editing?.category ?? 'UTILITY');
  const [language, setLanguage] = useState(editing?.language ?? 'it');
  const [headerType, setHeaderType] = useState(editing?.header_type ?? 'NONE');
  const [headerText, setHeaderText] = useState(editing?.header_text ?? '');
  const [body, setBody] = useState(editing?.body ?? '');
  const [footer, setFooter] = useState(editing?.footer ?? '');
  const [buttons, setButtons] = useState<TemplateButtonInput[]>(
    (editing?.buttons as TemplateButtonInput[] | undefined)?.map((b) => ({
      type: String(b.type),
      text: b.text ?? '',
      url: b.url ?? '',
      phone_number: b.phone_number ?? '',
    })) ?? [],
  );
  const [examples, setExamples] = useState<string[]>(editing?.body_examples ?? []);

  const variables = useMemo(() => extractVariables(body), [body]);

  const issues = useMemo(
    () =>
      lintTemplate({
        body,
        category,
        language,
        footer: footer || null,
        headerType,
        headerText: headerText || null,
        buttons,
        bodyExamples: variables.length ? examples : undefined,
      }),
    [body, category, language, footer, headerType, headerText, buttons, examples, variables.length],
  );
  const hasBlockingErrors = issues.some((i) => i.level === 'error');
  const warnings = issues.filter((i) => i.level === 'warning');

  const buildPayload = () => {
    const cleanButtons = buttons
      .filter((b) => b.type)
      .map((b) => {
        const out: TemplateButtonInput = { type: b.type, text: b.text || '' };
        if (b.type === 'URL' && b.url) out.url = b.url;
        if (b.type === 'PHONE_NUMBER' && b.phone_number) out.phone_number = b.phone_number;
        return out;
      });
    return {
      purpose,
      category,
      language,
      body,
      header_type: headerType,
      header_text: headerType === 'TEXT' ? headerText || null : null,
      footer: footer || null,
      buttons: cleanButtons.length ? cleanButtons : null,
      body_examples: variables.length ? variables.map((_, i) => examples[i] ?? '') : null,
    };
  };

  const create = useMutation({
    mutationFn: async (asDraft: boolean) => {
      const api = getApiClient();
      const { error } = await api.POST('/whatsapp-templates', {
        body: { ...buildPayload(), as_draft: asDraft } as never,
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: onDone,
  });

  const update = useMutation({
    mutationFn: async () => {
      if (!editing) return;
      const api = getApiClient();
      const { error } = await api.PUT('/whatsapp-templates/{template_id}', {
        params: { path: { template_id: editing.id } },
        body: buildPayload() as never,
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: onDone,
  });

  const busy = create.isPending || update.isPending;
  const mutationError = create.error ?? update.error;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {editing ? `Modifica «${editing.name}»` : 'Nuovo template'}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
        {/* ---- Colonna form ---- */}
        <div className="space-y-4">
          <TemplateGuidelines />

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="space-y-1">
              <Label>Scopo</Label>
              <select
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
              >
                {PURPOSES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label>Categoria</Label>
              <select
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">{CATEGORY_HINTS[category]}</p>
              <FieldIssues issues={issues} field="category" />
            </div>
            <div className="space-y-1">
              <Label htmlFor="lang">Lingua</Label>
              <Input id="lang" value={language} onChange={(e) => setLanguage(e.target.value)} />
              <p className="text-xs text-muted-foreground">Formato Meta: it, en, en_US…</p>
              <FieldIssues issues={issues} field="language" />
            </div>
          </div>

          {/* Header */}
          <div className="space-y-1">
            <Label>Intestazione (opzionale)</Label>
            <div className="flex gap-2">
              <select
                className="h-9 w-40 rounded-md border border-input bg-background px-3 text-sm"
                value={headerType}
                onChange={(e) => setHeaderType(e.target.value)}
              >
                <option value="NONE">Nessuna</option>
                <option value="TEXT">Testo</option>
              </select>
              {headerType === 'TEXT' ? (
                <Input
                  placeholder="Titolo del messaggio"
                  maxLength={MAX_HEADER_TEXT_LEN}
                  value={headerText}
                  onChange={(e) => setHeaderText(e.target.value)}
                />
              ) : null}
            </div>
            <FieldIssues issues={issues} field="header" />
          </div>

          {/* Body */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <Label htmlFor="body">Corpo del messaggio</Label>
              <span
                className={
                  body.length > MAX_BODY_LEN ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'
                }
              >
                {body.length}/{MAX_BODY_LEN}
              </span>
            </div>
            <Textarea
              id="body"
              rows={4}
              placeholder="Ciao {{1}}, possiamo riprendere da dove eravamo rimasti?"
              value={body}
              onChange={(e) => setBody(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Usa {'{{1}}'}, {'{{2}}'}… per le variabili. Sequenziali, mai a inizio/fine né due di fila.
            </p>
            <FieldIssues issues={issues} field="body" />
          </div>

          {/* Esempi per variabile */}
          {variables.length > 0 ? (
            <div className="space-y-2 rounded-md border border-input bg-muted/30 p-3">
              <Label className="text-xs uppercase tracking-wide text-muted-foreground">
                Valori di esempio per le variabili
              </Label>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {variables.map((n, i) => (
                  <div key={n} className="flex items-center gap-2">
                    <span className="w-10 shrink-0 text-xs font-mono text-muted-foreground">{`{{${n}}}`}</span>
                    <Input
                      placeholder={`esempio per {{${n}}}`}
                      value={examples[i] ?? ''}
                      onChange={(e) => {
                        const next = [...examples];
                        next[i] = e.target.value;
                        setExamples(next);
                      }}
                    />
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* Footer */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <Label htmlFor="footer">Footer (opzionale)</Label>
              <span className="text-xs text-muted-foreground">
                {footer.length}/{MAX_FOOTER_LEN}
              </span>
            </div>
            <Input
              id="footer"
              maxLength={MAX_FOOTER_LEN}
              value={footer}
              onChange={(e) => setFooter(e.target.value)}
            />
            <FieldIssues issues={issues} field="footer" />
          </div>

          {/* Pulsanti */}
          <ButtonsEditor buttons={buttons} setButtons={setButtons} issues={issues} />

          {mutationError ? (
            <p className="text-sm text-destructive">
              {mutationError instanceof Error ? mutationError.message : 'Errore'}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center justify-end gap-3">
            {hasBlockingErrors ? (
              <span className="text-xs text-destructive">
                Correggi gli errori evidenziati prima di salvare.
              </span>
            ) : warnings.length ? (
              <span className="text-xs text-amber-600 dark:text-amber-500">
                {warnings.length} avviso/i non bloccante/i.
              </span>
            ) : null}

            {editing ? (
              <Button onClick={() => update.mutate()} disabled={busy || !body.trim() || hasBlockingErrors}>
                {update.isPending ? 'Salvataggio…' : 'Salva modifiche'}
              </Button>
            ) : (
              <>
                <Button
                  variant="outline"
                  onClick={() => create.mutate(true)}
                  disabled={busy || !body.trim() || hasBlockingErrors}
                >
                  Salva come bozza
                </Button>
                <Button
                  onClick={() => create.mutate(false)}
                  disabled={busy || !body.trim() || hasBlockingErrors}
                >
                  {create.isPending ? 'Invio…' : 'Crea e invia per approvazione'}
                </Button>
              </>
            )}
          </div>
        </div>

        {/* ---- Colonna anteprima ---- */}
        <div className="lg:sticky lg:top-4 lg:self-start">
          <WhatsAppTemplatePreview
            body={body}
            headerType={headerType}
            headerText={headerText}
            footer={footer}
            buttons={buttons}
            examples={examples}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function ButtonsEditor({
  buttons,
  setButtons,
  issues,
}: {
  buttons: TemplateButtonInput[];
  setButtons: (b: TemplateButtonInput[]) => void;
  issues: LintIssue[];
}) {
  const update = (i: number, patch: Partial<TemplateButtonInput>) => {
    const next = buttons.map((b, idx) => (idx === i ? { ...b, ...patch } : b));
    setButtons(next);
  };
  const remove = (i: number) => setButtons(buttons.filter((_, idx) => idx !== i));
  const add = () => setButtons([...buttons, { type: 'QUICK_REPLY', text: '' }]);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>Pulsanti (opzionale)</Label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={add}
          disabled={buttons.length >= MAX_BUTTONS_TOTAL}
        >
          + Aggiungi pulsante
        </Button>
      </div>
      {buttons.map((btn, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2 rounded-md border border-input p-2">
          <select
            className="h-9 w-44 rounded-md border border-input bg-background px-2 text-sm"
            value={btn.type}
            onChange={(e) => update(i, { type: e.target.value })}
          >
            {BUTTON_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          {btn.type !== 'COPY_CODE' ? (
            <Input
              className="w-40"
              placeholder="Etichetta"
              maxLength={25}
              value={btn.text ?? ''}
              onChange={(e) => update(i, { text: e.target.value })}
            />
          ) : null}
          {btn.type === 'URL' ? (
            <Input
              className="flex-1"
              placeholder="https://…"
              value={btn.url ?? ''}
              onChange={(e) => update(i, { url: e.target.value })}
            />
          ) : null}
          {btn.type === 'PHONE_NUMBER' ? (
            <Input
              className="w-44"
              placeholder="+39…"
              value={btn.phone_number ?? ''}
              onChange={(e) => update(i, { phone_number: e.target.value })}
            />
          ) : null}
          <Button type="button" variant="ghost" size="sm" onClick={() => remove(i)}>
            Rimuovi
          </Button>
        </div>
      ))}
      <FieldIssues issues={issues} field="buttons" />
    </div>
  );
}
