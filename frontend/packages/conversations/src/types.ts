export type MessageStatus = 'pending' | 'sent' | 'delivered' | 'read' | 'failed';
export type MessageDirection = 'in' | 'out';
export type MessageRole = 'user' | 'assistant' | 'agent';

export interface Conversation {
  id: string;
  merchant_id: string;
  lead_id?: string | null;
  wa_contact_phone: string | null;
  wa_phone_number_id: string | null;
  status: string;
  last_message_at: string | null;
  /** Time of the customer's last inbound — drives the 24h-window composer banner. */
  last_inbound_at?: string | null;
  message_count: number;
  /** Per-thread bot takeover. AND-ed with merchant `bot.auto_reply_enabled`. */
  auto_reply: boolean;
  /** Soft-pause with auto-resume (ISO). In the future = bot silenced until then. */
  ai_disabled_until?: string | null;
  /** Operator who took the thread over (auto-takeover or timed pause). */
  assigned_to?: string | null;
  /** Why the thread was handed off (e.g. "manual_reply", "video_message", "angry"). */
  handoff_reason?: string | null;
  /** The AI's 1-2 sentence brief for the operator on escalation. */
  handoff_summary?: string | null;
  /** When the handoff started / was resolved (ISO). */
  handoff_at?: string | null;
  handoff_resolved_at?: string | null;
  /** Agent's free-text internal note, shown in the detail panel. NULL when empty. */
  internal_note?: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
  // Hydrated client-side from the latest message in the thread
  last_message_preview?: string | null;
  last_message_role?: MessageRole | null;
  last_message_sender_type?: SenderType | null;
  unread_count?: number;
}

/**
 * Chi ha prodotto il messaggio.
 *
 * `customer` — turno in entrata dal lead. `phone` — dall'app WhatsApp Business
 * sul telefono del merchant (echo Coexistence). `human` — risposta scritta dal
 * composer web. `ai` — turno dell'assistente. `agent_action` — invio partito da
 * un'azione dell'agente (es. proposta di slot). `automation` / `automation_ai` /
 * `appointment_reminder` — invii proattivi da lavagnetta e scheduler.
 *
 * La fonte di verità è il CHECK constraint `ck_messages_sender_type`
 * (migrazione 0047), che rimpiazza la vecchia chiave JSONB `meta.sender_type`.
 * Questo union NON viene da OpenAPI: l'inbox legge i messaggi **direttamente da
 * Supabase** (spec 4.4), quindi va tenuto allineato a mano — ed era già
 * divergente, perché elencava sei valori mentre il backend ne scriveva otto:
 * `customer` e `agent_action` mancavano, e un messaggio inviato da un'azione
 * dell'agente non matchava nessun ramo della UI.
 */
export type SenderType =
  | 'customer'
  | 'phone'
  | 'human'
  | 'ai'
  | 'agent_action'
  | 'automation'
  | 'automation_ai'
  | 'appointment_reminder';

/** True when the message was sent by an automation/scheduler, not a person or
 *  the inbound-reply bot — drives the "Automazione" labeling in the inbox. */
export function isAutomationSender(senderType: SenderType | null | undefined): boolean {
  return (
    senderType === 'automation' ||
    senderType === 'automation_ai' ||
    senderType === 'appointment_reminder'
  );
}

/** Inbound media kinds a message can carry (image/audio/video/document/sticker). */
export type MediaKind = 'image' | 'audio' | 'video' | 'document' | 'sticker';

/**
 * Attachment descriptor written by the worker under `meta.media`. `storage_path`
 * is null until the download lands (two-phase write) — the bubble shows a
 * "non disponibile" placeholder until then. `error` is set when the download or
 * store failed; `transcription` carries the Whisper text for voice notes.
 */
export interface MessageMediaMeta {
  kind?: MediaKind;
  mime?: string | null;
  wa_media_id?: string | null;
  storage_path?: string | null;
  size_bytes?: number | null;
  caption?: string | null;
  transcription?: string | null;
  error?: string | null;
}

export interface MessageMeta {
  sender_type?: SenderType;
  media?: MessageMediaMeta | null;
  [key: string]: unknown;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  direction: MessageDirection;
  content: string;
  status: MessageStatus;
  client_message_id: string | null;
  wa_message_id: string | null;
  delivered_at: string | null;
  read_at: string | null;
  failed_at: string | null;
  error: Record<string, unknown> | null;
  /**
   * Colonna dalla migrazione 0047, fonte di verità. Opzionale perché le righe
   * lette prima del deploy del backend possono non averla; usa `senderTypeOf`
   * invece di leggerla direttamente, che ricade su `meta.sender_type`.
   */
  sender_type?: SenderType | null;
  /** Attribuzione last-touch: l'invio a cui questo messaggio risponde. */
  reply_to_message_id?: string | null;
  /** Attribuzione: quale automazione e quale nodo hanno prodotto l'invio. */
  automation_id?: string | null;
  automation_node_key?: string | null;
  meta?: MessageMeta | null;
  created_at: string;
}

/** Legge il mittente dalla colonna, con fallback sul vecchio JSONB.
 *
 * Il backend scrive entrambi per una release: l'inbox legge da Supabase in
 * diretta e in Realtime, quindi durante la finestra di deploy convivono righe
 * scritte da versioni diverse.
 */
export function senderTypeOf(message: Pick<Message, 'sender_type' | 'meta'>): SenderType | null {
  return message.sender_type ?? message.meta?.sender_type ?? null;
}

export interface ThreadFilters {
  status?: 'open' | 'closed' | 'all';
  merchantId?: string;
  search?: string;
}

/**
 * UI-facing status filter for the inbox thread-list tabs. Decoupled from the
 * raw DB `status` string so the tabs can fold (`status` + `auto_reply`) into
 * agent-meaningful buckets without leaking DB vocabulary into the UI.
 *
 *   all          — everything
 *   active       — bot/active threads (status === 'active', bot still answering)
 *   needs_human  — escalated, no human yet (auto_reply false, unassigned): waiting on an agent
 *   managed      — a human took over (auto_reply false, assigned_to set)
 *   resolved     — anything no longer active (closed/archived/…)
 */
export type InboxFilter = 'all' | 'active' | 'needs_human' | 'managed' | 'resolved';

/**
 * Lead linked to a conversation, surfaced in the detail panel. Mirrors the
 * `leads` table columns the panel reads directly via Supabase under RLS.
 * `tags` is not a DB column — it is read defensively from `meta.tags`.
 */
export interface Lead {
  id: string;
  name: string | null;
  email: string | null;
  phone: string;
  score: number;
  score_reasons: string[];
  sentiment: string | null;
  status: string;
  pipeline_stage_id: string | null;
  meta: Record<string, unknown> | null;
}

/** A detected sales objection tied to a conversation (objections table). */
export interface Objection {
  id: string;
  category: string;
  summary: string;
  quote: string | null;
  severity: string;
  created_at: string;
}
