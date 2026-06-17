'use client';

import { useState } from 'react';
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

  const templates = useQuery({
    queryKey: ['whatsapp-templates'],
    queryFn: async (): Promise<Template[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/whatsapp-templates');
      if (error) throw new Error(apiErrorMessage(error));
      return data as Template[];
    },
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['whatsapp-templates'] });

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

  return (
    <div className="space-y-4 p-6">
      <div className="flex justify-end">
        <Button onClick={() => setShowForm((v) => !v)}>
          {showForm ? 'Annulla' : 'Nuovo template'}
        </Button>
      </div>

      {showForm ? (
        <CreateTemplateForm
          onCreated={() => {
            setShowForm(false);
            void invalidate();
          }}
        />
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
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => syncTemplate.mutate(t.id)}
                  disabled={syncTemplate.isPending}
                >
                  Sincronizza stato
                </Button>
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

function CreateTemplateForm({ onCreated }: { onCreated: () => void }) {
  const [purpose, setPurpose] = useState('reactivation');
  const [category, setCategory] = useState('UTILITY');
  const [language, setLanguage] = useState('it');
  const [body, setBody] = useState('');
  const [footer, setFooter] = useState('');

  const create = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { error } = await api.POST('/whatsapp-templates', {
        body: {
          purpose,
          category,
          language,
          body,
          header_type: 'NONE',
          footer: footer || null,
        },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: onCreated,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Nuovo template</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
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
          </div>
          <div className="space-y-1">
            <Label htmlFor="lang">Lingua</Label>
            <Input id="lang" value={language} onChange={(e) => setLanguage(e.target.value)} />
          </div>
        </div>

        <div className="space-y-1">
          <Label htmlFor="body">Corpo del messaggio</Label>
          <Textarea
            id="body"
            rows={4}
            placeholder="Ciao {{1}}, possiamo riprendere da dove eravamo rimasti?"
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Usa {'{{1}}'}, {'{{2}}'}… per le variabili. Devono essere sequenziali e non a inizio/fine
            riga.
          </p>
        </div>

        <div className="space-y-1">
          <Label htmlFor="footer">Footer (opzionale)</Label>
          <Input
            id="footer"
            maxLength={60}
            value={footer}
            onChange={(e) => setFooter(e.target.value)}
          />
        </div>

        {create.error ? (
          <p className="text-sm text-destructive">
            {create.error instanceof Error ? create.error.message : 'Errore'}
          </p>
        ) : null}

        <div className="flex justify-end">
          <Button onClick={() => create.mutate()} disabled={create.isPending || !body.trim()}>
            {create.isPending ? 'Invio…' : 'Crea e invia per approvazione'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
