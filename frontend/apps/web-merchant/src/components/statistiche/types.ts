/** Tipi condivisi della pagina Statistiche (ADR 0021 + 0023).
 *
 * Le bolle hanno tre sorgenti e la distinzione **va mostrata**: le automatiche
 * escono dalle colonne di `messages` e funzionano senza configurare nulla; le
 * custom richiedono di dichiarare prima una statistica e poi cablarla in un nodo
 * `emit_outcome` di un'automazione. Se la UI le presenta tutte allo stesso modo,
 * il merchant cerca dove "cablare" i messaggi inviati e non lo trova mai.
 */

export type MetricSource = 'event' | 'messages' | 'outcome';

/** Una bolla configurata, come la salva il config cascade. */
export type MetricDefinition = {
  id: string;
  label: string;
  source: MetricSource;
  event_type?: string | null;
  outcome_id?: string | null;
  direction?: 'in' | 'out' | null;
  sender_types?: string[];
  has_reply?: boolean | null;
  automation_node_key?: string | null;
  window_days?: number | null;
  aggregation?: 'count' | 'count_unique';
};

/** Una bolla calcolata, come la restituisce `/analytics/metrics`. */
export type MetricValue = {
  id: string;
  label: string;
  source: MetricSource;
  window_days: number;
  value: number;
  event_type?: string | null;
  outcome_id?: string | null;
  /** Solo per le bolle `outcome`: quanti vengono da una sorgente certa. */
  verified?: number | null;
};

export type ConversationProfile = {
  id: string;
  key: string;
  name: string;
  description?: string | null;
  is_default: boolean;
  enabled: boolean;
  /** Riga di libreria d'agenzia: visibile e usabile, non modificabile dal merchant. */
  is_library: boolean;
  overrides: Record<string, unknown>;
};

export type OutcomeDefinition = {
  id: string;
  key: string;
  label: string;
  description?: string | null;
  source_kind: string;
  cardinality: string;
  enabled: boolean;
  is_library: boolean;
};

export type EventCatalogEntry = {
  event_type: string;
  label: string;
  description: string;
  category: string;
  selectable: boolean;
};

/** Bolle strutturali offerte dal builder. Rispecchiano
 *  `STRUCTURAL_METRIC_PRESETS` lato backend: inviati e risposti sono **lo stesso
 *  insieme** letto due volte (stessa direzione, stessi sender_types), ed è questo
 *  che rende il loro rapporto un tasso di risposta sensato. */
export const STRUCTURAL_PRESETS: MetricDefinition[] = [
  {
    id: 'automation_messages_sent',
    label: 'Messaggi inviati',
    source: 'messages',
    direction: 'out',
    sender_types: ['automation', 'automation_ai'],
  },
  {
    id: 'automation_replies_received',
    label: 'Risposte ricevute',
    source: 'messages',
    direction: 'out',
    sender_types: ['automation', 'automation_ai'],
    has_reply: true,
  },
  {
    id: 'automation_people_reached',
    label: 'Persone raggiunte',
    source: 'messages',
    direction: 'out',
    sender_types: ['automation', 'automation_ai'],
    aggregation: 'count_unique',
  },
  {
    id: 'inbound_messages',
    label: 'Messaggi ricevuti dai lead',
    source: 'messages',
    direction: 'in',
  },
];

export const SOURCE_LABELS: Record<MetricSource, string> = {
  messages: 'Automatica',
  event: 'Automatica',
  outcome: 'Personalizzata',
};
