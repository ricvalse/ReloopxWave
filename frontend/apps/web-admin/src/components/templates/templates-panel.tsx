'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  PageHeader,
} from '@reloop/ui';
import { getApiClient } from '@/lib/api';

type Template = components['schemas']['TemplateOut'];
type TemplateIn = components['schemas']['TemplateIn'];

const EMPTY_DRAFT: Draft = {
  id: null,
  name: '',
  description: '',
  defaultsJson: '{}',
  lockedKeysJson: '[]',
  isDefault: false,
};

type Draft = {
  id: string | null;
  name: string;
  description: string;
  defaultsJson: string;
  lockedKeysJson: string;
  isDefault: boolean;
};

export function TemplatesPanel() {
  const [draft, setDraft] = useState<Draft | null>(null);
  const queryClient = useQueryClient();

  const list = useQuery({
    queryKey: ['templates', 'list'],
    queryFn: async (): Promise<Template[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET(
        '/bot-config/templates' as never,
        {} as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Template[];
    },
  });

  return (
    <>
      <PageHeader
        title="Template bot"
        description="UC-10 — default e parametri che ogni merchant eredita per il suo bot."
        actions={
          <Button onClick={() => setDraft(draft ? null : { ...EMPTY_DRAFT })}>
            {draft && draft.id === null ? 'Annulla' : '+ Nuovo template'}
          </Button>
        }
      />
      {draft ? (
        <TemplateEditor
          key={draft.id ?? 'new'}
          draft={draft}
          onClose={() => setDraft(null)}
          onSaved={() => {
            setDraft(null);
            void queryClient.invalidateQueries({ queryKey: ['templates', 'list'] });
          }}
        />
      ) : null}
      <TemplateList
        query={list}
        onEdit={(t) =>
          setDraft({
            id: t.id,
            name: t.name,
            description: t.description ?? '',
            defaultsJson: stringifyJson(t.defaults),
            lockedKeysJson: stringifyJson(t.locked_keys),
            isDefault: t.is_default,
          })
        }
      />
    </>
  );
}

function TemplateList({
  query,
  onEdit,
}: {
  query: ReturnType<typeof useQuery<Template[]>>;
  onEdit: (t: Template) => void;
}) {
  if (query.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Caricamento template…</div>;
  }
  if (query.isError) {
    return (
      <div className="p-6 text-sm text-destructive">
        {query.error instanceof Error ? query.error.message : 'Errore sconosciuto'}
      </div>
    );
  }
  const templates = query.data ?? [];
  if (templates.length === 0) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Nessun template creato. Usa <strong>+ Nuovo template</strong> per definire i default
            dell&apos;agenzia.
          </CardContent>
        </Card>
      </div>
    );
  }
  return (
    <div className="space-y-3 p-6">
      {templates.map((t) => (
        <Card key={t.id}>
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardTitle className="flex items-center gap-2">
                {t.name}
                {t.is_default ? (
                  <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary ring-1 ring-primary/20">
                    Default
                  </span>
                ) : null}
              </CardTitle>
              {t.description ? (
                <p className="mt-1 text-sm text-muted-foreground">{t.description}</p>
              ) : null}
            </div>
            <Button variant="outline" size="sm" onClick={() => onEdit(t)}>
              Modifica
            </Button>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm md:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">Chiavi bloccate</dt>
                <dd>{t.locked_keys.length}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Default impostati</dt>
                <dd>{Object.keys(t.defaults).length}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">ID</dt>
                <dd className="font-mono text-xs">{t.id.slice(0, 8)}…</dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function TemplateEditor({
  draft,
  onClose,
  onSaved,
}: {
  draft: Draft;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [state, setState] = useState<Draft>(draft);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    setState(draft);
  }, [draft]);

  const parsed = useMemo(() => parseDraft(state), [state]);

  const save = useMutation({
    mutationFn: async (): Promise<Template> => {
      if (parsed.error) throw new Error(parsed.error);
      const body: TemplateIn = {
        name: state.name,
        description: state.description || null,
        defaults: parsed.defaults,
        locked_keys: parsed.lockedKeys,
        is_default: state.isDefault,
      };
      const api = getApiClient();
      if (state.id === null) {
        const { data, error } = await api.POST('/bot-config/templates' as never, {
          body,
        } as never);
        if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
        return data as Template;
      }
      const { data, error } = await api.PUT('/bot-config/templates/{template_id}' as never, {
        params: { path: { template_id: state.id } },
        body,
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Template;
    },
    onSuccess: onSaved,
    onError: (err) => setLocalError(err instanceof Error ? err.message : 'Errore salvataggio'),
  });

  const isNew = state.id === null;

  return (
    <div className="p-6">
      <Card>
        <CardHeader>
          <CardTitle>{isNew ? 'Nuovo template' : `Modifica: ${draft.name}`}</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              setLocalError(null);
              save.mutate();
            }}
          >
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="tmpl-name">
                  Nome
                </label>
                <input
                  id="tmpl-name"
                  required
                  value={state.name}
                  onChange={(e) => setState({ ...state, name: e.target.value })}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>
              <div className="flex items-center gap-2 self-end pb-2">
                <input
                  id="tmpl-is-default"
                  type="checkbox"
                  checked={state.isDefault}
                  onChange={(e) => setState({ ...state, isDefault: e.target.checked })}
                />
                <label htmlFor="tmpl-is-default" className="text-sm">
                  Default del tenant (applicato ai nuovi merchant)
                </label>
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="tmpl-desc">
                Descrizione
              </label>
              <input
                id="tmpl-desc"
                value={state.description}
                onChange={(e) => setState({ ...state, description: e.target.value })}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="tmpl-defaults">
                Default (JSON) — validato lato backend contro BotConfigSchema
              </label>
              <textarea
                id="tmpl-defaults"
                required
                rows={10}
                value={state.defaultsJson}
                onChange={(e) => setState({ ...state, defaultsJson: e.target.value })}
                className="block w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs"
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="tmpl-locked">
                Chiavi bloccate (JSON array di stringhe)
              </label>
              <textarea
                id="tmpl-locked"
                rows={3}
                value={state.lockedKeysJson}
                onChange={(e) => setState({ ...state, lockedKeysJson: e.target.value })}
                className="block w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs"
                placeholder='["rag.top_k", "scoring.hot_threshold"]'
              />
            </div>
            {parsed.error ? (
              <p className="text-sm text-destructive">{parsed.error}</p>
            ) : localError ? (
              <p className="text-sm text-destructive">{localError}</p>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={onClose} disabled={save.isPending}>
                Annulla
              </Button>
              <Button type="submit" disabled={save.isPending || !state.name || !!parsed.error}>
                {save.isPending ? 'Salvataggio…' : isNew ? 'Crea' : 'Salva'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function stringifyJson(v: unknown): string {
  return JSON.stringify(v ?? {}, null, 2);
}

type Parsed = { defaults: Record<string, unknown>; lockedKeys: string[]; error: string | null };

function parseDraft(state: Draft): Parsed {
  let defaults: Record<string, unknown> = {};
  let lockedKeys: string[] = [];
  try {
    const parsed = JSON.parse(state.defaultsJson || '{}');
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { defaults, lockedKeys, error: 'Defaults deve essere un oggetto JSON.' };
    }
    defaults = parsed as Record<string, unknown>;
  } catch (e) {
    return {
      defaults,
      lockedKeys,
      error: `JSON defaults non valido: ${e instanceof Error ? e.message : 'sintassi'}.`,
    };
  }
  try {
    const parsed = JSON.parse(state.lockedKeysJson || '[]');
    if (!Array.isArray(parsed) || !parsed.every((k) => typeof k === 'string')) {
      return { defaults, lockedKeys, error: 'Chiavi bloccate: deve essere un array di stringhe.' };
    }
    lockedKeys = parsed as string[];
  } catch (e) {
    return {
      defaults,
      lockedKeys,
      error: `JSON locked_keys non valido: ${e instanceof Error ? e.message : 'sintassi'}.`,
    };
  }
  return { defaults, lockedKeys, error: null };
}
