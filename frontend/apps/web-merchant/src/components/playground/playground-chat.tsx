'use client';

import { useState } from 'react';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { Send, Sparkles } from 'lucide-react';
import { getApiClient } from '@/lib/api';

type Message = { role: 'user' | 'assistant'; content: string };

const DEFAULT_SYSTEM_PROMPT =
  'Sei un assistente conversazionale italiano. Rispondi breve, cortese, professionale.';

export function PlaygroundChat() {
  const [systemPrompt, setSystemPrompt] = useState(DEFAULT_SYSTEM_PROMPT);
  const [history, setHistory] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [variantId, setVariantId] = useState('');
  const [useKb, setUseKb] = useState(true);
  const [pending, setPending] = useState(false);
  const [lastMeta, setLastMeta] = useState<{
    model?: string;
    tokens_in?: number;
    tokens_out?: number;
    latency_ms?: number;
    actions?: { kind: string; payload: Record<string, unknown> }[];
    retrieved_chunks?: { snippet: string; score: number }[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = async () => {
    if (!input.trim() || pending) return;
    setPending(true);
    setError(null);

    const newHistory: Message[] = [...history, { role: 'user', content: input }];
    setHistory(newHistory);
    setInput('');

    try {
      const api = getApiClient();
      const { data, error: apiError } = await api.POST('/playground/turn' as never, {
        body: {
          system_prompt: systemPrompt,
          history: newHistory.slice(0, -1), // exclude the just-sent user turn
          user_message: input,
          variant_id: variantId || null,
          use_kb: useKb,
        },
      } as never);
      if (apiError) throw new Error(typeof apiError === 'string' ? apiError : JSON.stringify(apiError));
      const payload = data as {
        reply_text: string;
        model: string;
        tokens_in: number;
        tokens_out: number;
        latency_ms: number;
        actions: { kind: string; payload: Record<string, unknown> }[];
        retrieved_chunks: { snippet: string; score: number }[];
      };
      setHistory([...newHistory, { role: 'assistant', content: payload.reply_text }]);
      setLastMeta({
        model: payload.model,
        tokens_in: payload.tokens_in,
        tokens_out: payload.tokens_out,
        latency_ms: payload.latency_ms,
        actions: payload.actions,
        retrieved_chunks: payload.retrieved_chunks,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(false);
    }
  };

  const reset = () => {
    setHistory([]);
    setLastMeta(null);
    setError(null);
  };

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_280px]">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" />
            Chat simulator
          </CardTitle>
          <Button variant="ghost" size="sm" onClick={reset}>
            Pulisci
          </Button>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex min-h-[320px] flex-col gap-3">
            {history.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Scrivi un messaggio per simulare una conversazione in entrata.
              </p>
            ) : (
              history.map((m, i) => (
                <div
                  key={i}
                  className={
                    m.role === 'user'
                      ? 'ml-auto max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground'
                      : 'mr-auto max-w-[80%] rounded-lg bg-muted px-3 py-2 text-sm'
                  }
                >
                  {m.content}
                </div>
              ))
            )}
          </div>
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder="Scrivi…"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
            <Button disabled={pending || !input.trim()} onClick={send}>
              <Send className="h-4 w-4" />
            </Button>
          </div>
          {error ? <p className="mt-2 text-sm text-destructive">{error}</p> : null}
        </CardContent>
      </Card>

      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Parametri</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <label className="text-xs font-medium text-muted-foreground">System prompt</label>
              <textarea
                rows={6}
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                className="mt-1 w-full rounded-md border border-input bg-background p-2 text-sm"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Variant id</label>
              <input
                value={variantId}
                onChange={(e) => setVariantId(e.target.value)}
                placeholder="default"
                className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={useKb} onChange={(e) => setUseKb(e.target.checked)} />
              Usa knowledge base
            </label>
          </CardContent>
        </Card>

        {lastMeta ? (
          <Card>
            <CardHeader>
              <CardTitle>Ultimo turno</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs text-muted-foreground">
              <div>
                <span className="font-medium">Modello:</span> {lastMeta.model}
              </div>
              <div>
                <span className="font-medium">Token:</span> {lastMeta.tokens_in} → {lastMeta.tokens_out}
              </div>
              <div>
                <span className="font-medium">Latenza:</span> {lastMeta.latency_ms} ms
              </div>
              {lastMeta.actions && lastMeta.actions.length > 0 ? (
                <div>
                  <span className="font-medium">Azioni:</span>{' '}
                  {lastMeta.actions.map((a) => a.kind).join(', ')}
                </div>
              ) : null}
              {lastMeta.retrieved_chunks && lastMeta.retrieved_chunks.length > 0 ? (
                <details>
                  <summary className="cursor-pointer font-medium">
                    KB ({lastMeta.retrieved_chunks.length})
                  </summary>
                  <ul className="mt-2 space-y-1">
                    {lastMeta.retrieved_chunks.map((c, i) => (
                      <li key={i} className="border-l-2 border-muted-foreground/20 pl-2">
                        <span className="font-mono">{c.score.toFixed(2)}</span> {c.snippet}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
