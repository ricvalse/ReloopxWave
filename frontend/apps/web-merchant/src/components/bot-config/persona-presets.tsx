'use client';

import { useQuery } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

type TonePreset = components['schemas']['TonePreset'];
type SuggestedRules = components['schemas']['SuggestedRules'];
type FormState = Record<string, unknown>;

export function PersonaPresets({
  form,
  onApplyValues,
  onAppendPhrase,
}: {
  form: FormState;
  onApplyValues: (values: Record<string, unknown>) => void;
  onAppendPhrase: (key: 'bot.do_phrases' | 'bot.dont_phrases', phrase: string) => void;
}) {
  const presetsQuery = useQuery({
    queryKey: ['bot-config', 'tone-presets'],
    staleTime: Infinity,
    queryFn: async (): Promise<TonePreset[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/tone-presets');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return (data as TonePreset[]) ?? [];
    },
  });
  const rulesQuery = useQuery({
    queryKey: ['bot-config', 'suggested-rules'],
    staleTime: Infinity,
    queryFn: async (): Promise<SuggestedRules> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/suggested-rules');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as SuggestedRules;
    },
  });

  const presets = presetsQuery.data ?? [];
  const rules = rulesQuery.data;
  const currentDo = Array.isArray(form['bot.do_phrases']) ? (form['bot.do_phrases'] as string[]) : [];
  const currentDont = Array.isArray(form['bot.dont_phrases'])
    ? (form['bot.dont_phrases'] as string[])
    : [];

  const isActive = (p: TonePreset) =>
    Object.entries(p.values as Record<string, unknown>).every(([k, v]) => form[k] === v);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Stile rapido</CardTitle>
        <p className="text-sm text-muted-foreground">
          Parti da un preset di tono, poi affina nei campi qui sotto. Le regole suggerite si
          aggiungono alle liste “da preferire / da evitare”.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Tono
          </p>
          <div className="flex flex-wrap gap-2">
            {presets.map((p) => {
              const active = isActive(p);
              return (
                <button
                  key={p.id}
                  type="button"
                  title={p.description}
                  onClick={() => onApplyValues(p.values as Record<string, unknown>)}
                  className={
                    'rounded-full border px-3 py-1 text-sm transition-colors ' +
                    (active
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-input hover:bg-accent')
                  }
                >
                  {p.label}
                </button>
              );
            })}
          </div>
        </div>

        {rules ? (
          <div className="grid gap-4 md:grid-cols-2">
            <RuleChips
              title="Regole da preferire"
              phrases={rules.do}
              current={currentDo}
              onAdd={(ph) => onAppendPhrase('bot.do_phrases', ph)}
            />
            <RuleChips
              title="Regole da evitare"
              phrases={rules.dont}
              current={currentDont}
              onAdd={(ph) => onAppendPhrase('bot.dont_phrases', ph)}
            />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RuleChips({
  title,
  phrases,
  current,
  onAdd,
}: {
  title: string;
  phrases: string[];
  current: string[];
  onAdd: (phrase: string) => void;
}) {
  return (
    <div>
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <div className="flex flex-wrap gap-2">
        {phrases.map((ph) => {
          const added = current.includes(ph);
          return (
            <button
              key={ph}
              type="button"
              disabled={added}
              onClick={() => onAdd(ph)}
              className={
                'rounded-full border px-3 py-1 text-left text-xs transition-colors ' +
                // Tokens, not a fixed Tailwind palette: the app boots in dark
                // mode, where emerald-50 on emerald-700 is an unreadable light
                // chip glued onto a dark card. Outlined rather than filled —
                // "already added" is a quiet state, not a call to action.
                (added
                  ? 'cursor-default border-success text-success'
                  : 'border-dashed border-input hover:bg-accent')
              }
            >
              {added ? '✓ ' : '+ '}
              {ph}
            </button>
          );
        })}
      </div>
    </div>
  );
}
