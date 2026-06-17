'use client';

import { useEffect, useRef, useState } from 'react';
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { Send, Sparkles } from 'lucide-react';
import { getApiClient } from '@/lib/api';

type ChatTurn = { role: 'user' | 'assistant'; content: string };

type EventData = { kind: string; summary: string; detail: Record<string, unknown> };

type DisplayItem =
  | { id: number; kind: 'user' | 'bot'; text: string }
  | { id: number; kind: 'event'; event: EventData };

type LeadState = {
  lead_score: number;
  lead_sentiment: string | null;
  lead_name: string | null;
  lead_email: string | null;
  pipeline_stage: string | null;
  booked: boolean;
  escalated: boolean;
  turn_count: number;
};

type Bubble = { text: string; delay_ms: number };

type TurnMeta = {
  model?: string;
  tokens_in?: number;
  tokens_out?: number;
  latency_ms?: number;
  retrieved_chunks?: { snippet: string; score: number }[];
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const EVENT_ICON: Record<string, string> = {
  book_slot: '📅',
  move_pipeline: '🔀',
  update_score: '📊',
  escalate_human: '🙋',
};

const SENTIMENT_LABEL: Record<string, string> = {
  positive: 'positivo',
  neutral: 'neutro',
  negative: 'negativo',
};

export function PlaygroundChat() {
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [items, setItems] = useState<DisplayItem[]>([]);
  const [input, setInput] = useState('');
  const [leadState, setLeadState] = useState<LeadState | null>(null);
  const [temperature, setTemperature] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  const [pending, setPending] = useState(false);
  const [lastMeta, setLastMeta] = useState<TurnMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const idRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const nextId = () => ++idRef.current;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [items, typing]);

  const send = async () => {
    const text = input.trim();
    if (!text || pending) return;
    setPending(true);
    setError(null);

    const priorHistory = history;
    setItems((prev) => [...prev, { id: nextId(), kind: 'user', text }]);
    setInput('');

    try {
      const api = getApiClient();
      // Dry-run: only the conversation + carried simulated state travel. Prompt,
      // settings, tools and scoring are resolved server-side, identical to a real turn.
      const { data, error: apiError } = await api.POST('/playground/turn' as never, {
        body: {
          history: priorHistory,
          user_message: text,
          state: leadState ?? undefined,
        },
      } as never);
      if (apiError) throw new Error(typeof apiError === 'string' ? apiError : JSON.stringify(apiError));

      const payload = data as {
        reply_text: string;
        bubbles: Bubble[];
        typing_indicator: boolean;
        events: EventData[];
        state: LeadState;
        model: string;
        tokens_in: number;
        tokens_out: number;
        latency_ms: number;
        retrieved_chunks: { snippet: string; score: number }[];
      };

      // LLM history stays clean: one assistant entry = the full reply (bubbles are
      // presentation only; the booking confirmation is a side-channel message).
      setHistory([
        ...priorHistory,
        { role: 'user', content: text },
        { role: 'assistant', content: payload.reply_text },
      ]);

      // Play the reply (and any booking confirmation) bubble-by-bubble with the
      // server-computed delays, exactly as production would deliver on WhatsApp.
      // The typing indicator stays visible for the whole sequence (bubbles pop
      // above it) so it never flickers between bubbles.
      const bubbles = payload.bubbles?.length
        ? payload.bubbles
        : [{ text: payload.reply_text, delay_ms: 0 }];
      const showTyping = payload.typing_indicator && bubbles.some((b) => b.delay_ms > 0);
      if (showTyping) setTyping(true);
      for (const bubble of bubbles) {
        if (bubble.delay_ms > 0) await sleep(bubble.delay_ms);
        setItems((prev) => [...prev, { id: nextId(), kind: 'bot', text: bubble.text }]);
      }
      setTyping(false);

      // Simulated tool outcomes as inline event chips (not part of LLM history).
      if (payload.events?.length) {
        setItems((prev) => [
          ...prev,
          ...payload.events.map((event) => ({ id: nextId(), kind: 'event' as const, event })),
        ]);
      }

      setLeadState(payload.state);
      const scoreEvent = payload.events?.find((e) => e.kind === 'update_score');
      if (scoreEvent && typeof scoreEvent.detail.temperature === 'string') {
        setTemperature(scoreEvent.detail.temperature);
      }
      setLastMeta({
        model: payload.model,
        tokens_in: payload.tokens_in,
        tokens_out: payload.tokens_out,
        latency_ms: payload.latency_ms,
        retrieved_chunks: payload.retrieved_chunks,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setTyping(false);
      setPending(false);
    }
  };

  const reset = () => {
    setHistory([]);
    setItems([]);
    setLeadState(null);
    setTemperature(null);
    setLastMeta(null);
    setError(null);
  };

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_300px]">
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
          <div className="mb-4 flex min-h-[400px] flex-col gap-3">
            {items.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Scrivi un messaggio per simulare una conversazione cliente reale. Il bot usa lo
                stesso prompt e le stesse impostazioni del sistema reale; le azioni (prenotazione,
                pipeline, scoring, escalation) vengono <strong>simulate</strong> — nessun effetto
                reale, nessun messaggio inviato o salvato.
              </p>
            ) : (
              items.map((item) =>
                item.kind === 'event' ? (
                  <div key={item.id} className="mx-auto max-w-[90%] text-center">
                    <span className="inline-block rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground">
                      {EVENT_ICON[item.event.kind] ?? '•'} {item.event.summary}
                    </span>
                  </div>
                ) : (
                  <div
                    key={item.id}
                    className={
                      item.kind === 'user'
                        ? 'ml-auto max-w-[80%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground'
                        : 'mr-auto max-w-[80%] whitespace-pre-wrap rounded-lg bg-muted px-3 py-2 text-sm'
                    }
                  >
                    {item.text}
                  </div>
                ),
              )
            )}
            {typing ? (
              <div className="mr-auto rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
                <span className="animate-pulse">sta scrivendo…</span>
              </div>
            ) : null}
            <div ref={bottomRef} />
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
            <CardTitle className="text-sm">Stato lead simulato</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-xs">
            {leadState ? (
              <>
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-muted-foreground">Lead score</span>
                    <span className="font-medium">
                      {leadState.lead_score}/100
                      {temperature ? (
                        <Badge
                          variant={
                            temperature === 'hot'
                              ? 'success'
                              : temperature === 'warm'
                                ? 'warning'
                                : 'secondary'
                          }
                          className="ml-2"
                        >
                          {temperature}
                        </Badge>
                      ) : null}
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className={
                        temperature === 'hot'
                          ? 'h-full bg-success'
                          : temperature === 'warm'
                            ? 'h-full bg-warning'
                            : 'h-full bg-muted-foreground/40'
                      }
                      style={{ width: `${Math.max(0, Math.min(100, leadState.lead_score))}%` }}
                    />
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Sentiment</span>
                  {leadState.lead_sentiment ? (
                    <Badge
                      variant={
                        leadState.lead_sentiment === 'positive'
                          ? 'success'
                          : leadState.lead_sentiment === 'negative'
                            ? 'destructive'
                            : 'secondary'
                      }
                    >
                      {SENTIMENT_LABEL[leadState.lead_sentiment] ?? leadState.lead_sentiment}
                    </Badge>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Nome</span>
                  <span className="font-medium">{leadState.lead_name ?? '—'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Email</span>
                  <span className="font-medium">{leadState.lead_email ?? '—'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Pipeline</span>
                  <span className="font-medium">{leadState.pipeline_stage ?? '—'}</span>
                </div>

                {(leadState.booked || leadState.escalated) && (
                  <div className="flex flex-wrap gap-1 pt-1">
                    {leadState.booked ? <Badge variant="success">Prenotato</Badge> : null}
                    {leadState.escalated ? <Badge variant="destructive">Escalation</Badge> : null}
                  </div>
                )}

                <p className="pt-1 text-[11px] text-muted-foreground">
                  Turno {leadState.turn_count} · simulazione, nessun dato reale scritto.
                </p>
              </>
            ) : (
              <p className="text-muted-foreground">
                Lo stato del lead comparirà qui e si aggiornerà a ogni turno (score, sentiment,
                identità, pipeline).
              </p>
            )}
          </CardContent>
        </Card>

        {lastMeta ? (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Dettagli tecnici</CardTitle>
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
              {lastMeta.retrieved_chunks && lastMeta.retrieved_chunks.length > 0 ? (
                <details>
                  <summary className="cursor-pointer font-medium">
                    Knowledge base ({lastMeta.retrieved_chunks.length})
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
