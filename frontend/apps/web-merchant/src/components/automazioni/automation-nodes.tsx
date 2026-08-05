'use client';

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Bolt, GitBranch, Send } from 'lucide-react';

export type NodeKind = 'trigger' | 'condition' | 'action';

export interface FieldDef {
  key: string;
  label: string;
  kind:
    | 'text'
    | 'number'
    | 'select'
    | 'keywords'
    | 'template'
    | 'clauses'
    | 'multiselect'
    | 'bool'
    // Riferimenti a entità del merchant, caricati in una tendina: mai digitati.
    | 'outcome'
    | 'profile';
  options?: { value: string; label: string }[];
  placeholder?: string;
}

export interface TypeDef {
  type: string;
  label: string;
  description?: string;
  fields: FieldDef[];
}

const TEMP_OPTIONS = [
  { value: 'hot', label: 'Caldo' },
  { value: 'warm', label: 'Tiepido' },
  { value: 'cold', label: 'Freddo' },
];
const EQ_OPTIONS = [
  { value: '==', label: 'uguale a' },
  { value: '!=', label: 'diverso da' },
];
const NUM_OPS = [
  { value: '>=', label: '≥' },
  { value: '<=', label: '≤' },
  { value: '>', label: '>' },
  { value: '<', label: '<' },
  { value: '==', label: '=' },
];

export const TRIGGER_DEFS: TypeDef[] = [
  { type: 'message_received', label: 'Messaggio ricevuto', description: 'Il lead scrive in chat.', fields: [] },
  {
    type: 'no_answer',
    label: 'Nessuna risposta',
    description:
      'Il lead è rimasto in silenzio, anche se non ha mai scritto. Oltre le 24h dall’ultimo suo messaggio serve un template approvato.',
    fields: [
      { key: 'delay_minutes', label: 'Ritardo 1° follow-up (min)', kind: 'number', placeholder: '120' },
      {
        key: 'source_template_id',
        label: 'Solo se l’ultimo messaggio era questo template',
        kind: 'template',
      },
    ],
  },
  { type: 'booking_created', label: 'Prenotazione creata', description: 'Appuntamento fissato.', fields: [] },
  { type: 'booking_failed', label: 'Prenotazione fallita', description: 'Tentativo non riuscito.', fields: [] },
  {
    type: 'lead_dormant',
    label: 'Lead dormiente',
    description: 'Inattivo da tempo.',
    fields: [{ key: 'days', label: 'Giorni di dormienza', kind: 'number', placeholder: '90' }],
  },
  {
    type: 'crm_lead_created',
    label: 'Nuovo lead dal CRM',
    description: 'Contatto creato in GoHighLevel.',
    fields: [],
  },
  {
    type: 'crm_opportunity_created',
    label: 'Nuovo lead in pipeline (CRM)',
    description: 'Opportunità creata in una pipeline GoHighLevel. Lascia vuoti i filtri per qualsiasi pipeline.',
    fields: [
      { key: 'pipeline_id', label: 'ID pipeline (opzionale)', kind: 'text', placeholder: 'qualsiasi pipeline' },
      { key: 'stage_id', label: 'ID stage (opzionale)', kind: 'text', placeholder: 'qualsiasi stage' },
    ],
  },
  {
    type: 'conversation_escalated',
    label: 'Handoff a operatore',
    description:
      'La conversazione è passata a un umano (richiesta del cliente, media non gestibile, o nodo «Passa a operatore»).',
    fields: [],
  },
  {
    type: 'conversation_handoff_overdue',
    label: 'Handoff in ritardo (SLA)',
    description: 'Un handoff è rimasto aperto oltre la soglia SLA senza essere preso in carico.',
    fields: [],
  },
];

export const CONDITION_DEFS: TypeDef[] = [
  {
    type: 'lead_temperature',
    label: 'Temperatura lead',
    fields: [
      { key: 'op', label: 'Operatore', kind: 'select', options: EQ_OPTIONS },
      { key: 'value', label: 'Valore', kind: 'select', options: TEMP_OPTIONS },
    ],
  },
  {
    type: 'lead_score',
    label: 'Punteggio lead',
    fields: [
      { key: 'op', label: 'Operatore', kind: 'select', options: NUM_OPS },
      { key: 'value', label: 'Punteggio', kind: 'number', placeholder: '80' },
    ],
  },
  { type: 'within_24h_window', label: 'Finestra 24h aperta', fields: [] },
  {
    type: 'time_of_day',
    label: 'Fascia oraria (UTC)',
    fields: [
      { key: 'from', label: 'Dalle', kind: 'text', placeholder: '09:00' },
      { key: 'to', label: 'Alle', kind: 'text', placeholder: '18:00' },
    ],
  },
  {
    type: 'message_contains',
    label: 'Messaggio contiene',
    fields: [{ key: 'keywords', label: 'Parole chiave', kind: 'keywords', placeholder: 'prezzo, costo' }],
  },
  {
    type: 'ai_check',
    label: 'Condizione AI',
    description: "L'AI valuta un tuo prompt sul contesto della conversazione e risponde sì/no.",
    fields: [
      {
        key: 'prompt',
        label: 'Prompt di verifica',
        kind: 'text',
        placeholder: 'Es. Il lead ha chiesto esplicitamente il prezzo?',
      },
      { key: 'model', label: 'Modello (opzionale)', kind: 'text', placeholder: 'auto' },
    ],
  },
  // --- Cancelli deterministici (ADR 0023) ---------------------------------
  // Costano zero e vanno messi PRIMA di una condizione AI: senza, un flusso su
  // «Messaggio ricevuto» fa girare l'AI su ogni messaggio del merchant, non solo
  // su quelli della campagna.
  {
    type: 'conversation_profile',
    label: 'Profilo attivo è…',
    description:
      'Vero solo se la conversazione sta usando quel profilo. Mettilo prima di una condizione AI per limitarla alla campagna.',
    fields: [{ key: 'profile_id', label: 'Profilo', kind: 'profile' }],
  },
  {
    type: 'last_touch_node',
    label: 'Sta rispondendo a…',
    description:
      'Vero se l’ultimo messaggio inviato dal bot è partito da quel nodo. Più affidabile delle parole chiave: se hai chiesto “hai compilato?”, il lead risponde “sì” e nessuna keyword corrisponderebbe.',
    fields: [
      {
        key: 'node_key',
        label: 'Nodo che ha inviato',
        kind: 'text',
        placeholder: 'es. n3 (identificativo del nodo di invio)',
      },
    ],
  },
  {
    type: 'has_outcome',
    label: 'Ha già l’esito…',
    description:
      'Vero se la statistica è già stata registrata per questo lead. Usalo negato: chi ha già confermato esce dal flusso e non costa più nulla.',
    fields: [{ key: 'outcome_id', label: 'Statistica', kind: 'outcome' }],
  },
  {
    type: 'condition_group',
    label: 'Se (E / O)',
    description: 'Combina più condizioni con E (tutte) oppure O (almeno una).',
    fields: [
      {
        key: 'operator',
        label: 'Combinazione',
        kind: 'select',
        options: [
          { value: 'and', label: 'Tutte (E)' },
          { value: 'or', label: 'Almeno una (O)' },
        ],
      },
      { key: 'clauses', label: 'Condizioni', kind: 'clauses' },
    ],
  },
];

// Condition defs selectable as a `condition_group` clause. Excludes only
// `condition_group` itself (no nesting); `ai_check` is included and evaluated
// asynchronously by the worker engine.
export const CLAUSE_CONDITION_DEFS: TypeDef[] = CONDITION_DEFS.filter(
  (d) => d.type !== 'condition_group',
);

const WINDOW_POLICY_OPTIONS = [
  { value: 'auto', label: 'Auto (testo se entro 24h, altrimenti template)' },
  { value: 'require_template', label: 'Solo template approvato' },
  { value: 'freeform_only', label: 'Solo testo libero (entro 24h)' },
];

const SET_FIELD_OPTIONS = [
  { value: 'tag', label: 'Tag' },
  { value: 'score_delta', label: 'Punteggio (delta)' },
  { value: 'custom_field', label: 'Campo personalizzato' },
];

// Subset of orchestrator ActionKinds an ai_reply node may let the AI dispatch.
// Mirrors AI_REPLY_DISPATCHABLE_ACTIONS in backend ai_core/automations.py.
const ALLOWED_ACTION_OPTIONS = [
  { value: 'propose_slots', label: 'Proponi slot' },
  { value: 'book_slot', label: 'Prenota appuntamento' },
  { value: 'reschedule_slot', label: 'Riprogramma' },
  { value: 'cancel_slot', label: 'Annulla appuntamento' },
  { value: 'move_pipeline', label: 'Avanza in pipeline' },
  { value: 'update_score', label: 'Aggiorna punteggio' },
  { value: 'escalate_human', label: 'Passa a operatore' },
];

export const ACTION_DEFS: TypeDef[] = [
  {
    type: 'send',
    label: 'Invia messaggio',
    description: 'Rispetta la finestra 24h: testo entro, template approvato fuori.',
    fields: [
      { key: 'window_policy', label: 'Politica finestra 24h', kind: 'select', options: WINDOW_POLICY_OPTIONS },
      { key: 'free_text', label: 'Testo libero (entro 24h)', kind: 'text', placeholder: 'Ciao {name}, …' },
      { key: 'template_id', label: 'Template approvato (fuori 24h)', kind: 'template' },
    ],
  },
  {
    type: 'ai_reply',
    label: 'Risposta AI',
    description: "L'AI genera e invia un messaggio mirato (testo entro 24h, template di fallback fuori).",
    fields: [
      { key: 'objective', label: 'Obiettivo', kind: 'text', placeholder: "Es. riproponi l'appuntamento" },
      { key: 'extra_instructions', label: 'Istruzioni extra', kind: 'text', placeholder: 'Tono, dettagli…' },
      { key: 'window_policy', label: 'Politica finestra 24h', kind: 'select', options: WINDOW_POLICY_OPTIONS },
      { key: 'fallback_template_id', label: 'Template di fallback (fuori 24h)', kind: 'template' },
      { key: 'allowed_actions', label: 'Azioni AI consentite', kind: 'multiselect', options: ALLOWED_ACTION_OPTIONS },
      { key: 'model_override', label: 'Modello (opzionale)', kind: 'text', placeholder: 'auto' },
    ],
  },
  {
    type: 'set_lead_field',
    label: 'Aggiorna lead/CRM',
    description: 'Aggiorna un campo del lead: tag, punteggio (delta) o campo personalizzato.',
    fields: [
      { key: 'field', label: 'Campo', kind: 'select', options: SET_FIELD_OPTIONS },
      { key: 'key', label: 'Nome campo (per campo personalizzato)', kind: 'text', placeholder: 'es. citta' },
      { key: 'value', label: 'Valore', kind: 'text', placeholder: 'es. VIP, oppure 10 / -5 per il punteggio' },
      { key: 'ghl_sync', label: 'Sincronizza su GHL', kind: 'bool', placeholder: 'Propaga tag/campo su GHL' },
    ],
  },
  {
    type: 'emit_outcome',
    label: 'Registra esito',
    description:
      'Registra una statistica personalizzata per questo lead. Non invia niente. È idempotente: se l’esito c’è già, non viene contato due volte.',
    fields: [
      { key: 'outcome_id', label: 'Statistica', kind: 'outcome' },
      {
        key: 'confidence',
        label: 'Confidenza (0-1, opzionale)',
        kind: 'number',
        placeholder: '0.8',
      },
    ],
  },
  {
    type: 'set_conversation_profile',
    label: 'Carica profilo',
    description:
      'Cambia il comportamento del bot su questa conversazione fino a fine chat, poi si torna al profilo di default.',
    fields: [{ key: 'profile_id', label: 'Profilo', kind: 'profile' }],
  },
  {
    type: 'human_handoff',
    label: 'Passa a operatore',
    description: 'Mette la conversazione in gestione umana (takeover): l’AI smette di rispondere.',
    fields: [
      { key: 'reason', label: 'Motivo', kind: 'text', placeholder: 'es. richiesta complessa' },
    ],
  },
  {
    type: 'notify_slack',
    label: 'Notifica Slack',
    description:
      'Invia un avviso al canale Slack collegato (Integrazioni → Slack). Non invia messaggi WhatsApp.',
    fields: [
      {
        key: 'text',
        label: 'Testo personalizzato (opzionale)',
        kind: 'text',
        placeholder: 'Vuoto = avviso automatico. Placeholder: {name} {phone} {reason} {last_message}',
      },
    ],
  },
  {
    type: 'wait',
    label: 'Attendi',
    fields: [
      { key: 'minutes', label: 'Durata', kind: 'number', placeholder: '60' },
      {
        key: 'unit',
        label: 'Unità',
        kind: 'select',
        options: [
          { value: 'minutes', label: 'Minuti' },
          { value: 'hours', label: 'Ore' },
          { value: 'days', label: 'Giorni' },
        ],
      },
    ],
  },
  {
    type: 'wait_until_before',
    label: 'Attendi fino a … prima dell’appuntamento',
    description: "Programma l'invio N ore prima dell'orario dell'appuntamento (promemoria).",
    fields: [{ key: 'hours', label: 'Ore prima', kind: 'number', placeholder: '24' }],
  },
  {
    type: 'send_template',
    label: 'Invia template (legacy)',
    fields: [{ key: 'template_id', label: 'Template approvato', kind: 'template' }],
  },
  {
    type: 'send_message',
    label: 'Invia testo (legacy)',
    description: 'Testo libero (solo entro la finestra 24h).',
    fields: [{ key: 'text', label: 'Testo', kind: 'text', placeholder: 'Ciao {name}, …' }],
  },
];

export const DEFS_BY_KIND: Record<NodeKind, TypeDef[]> = {
  trigger: TRIGGER_DEFS,
  condition: CONDITION_DEFS,
  action: ACTION_DEFS,
};

export function findDef(kind: NodeKind, type: string): TypeDef | undefined {
  return DEFS_BY_KIND[kind]?.find((d) => d.type === type);
}

export function nodeSummary(kind: NodeKind, type: string, config: Record<string, unknown>): string {
  if (kind === 'condition') {
    if (type === 'lead_temperature') return `${config.op ?? '=='} ${config.value ?? '—'}`;
    if (type === 'lead_score') return `score ${config.op ?? '>='} ${config.value ?? '—'}`;
    if (type === 'time_of_day') return `${config.from ?? '—'} → ${config.to ?? '—'}`;
    if (type === 'message_contains') {
      const kw = config.keywords;
      return Array.isArray(kw) ? kw.join(', ') : '';
    }
    if (type === 'within_24h_window') return 'finestra aperta';
    if (type === 'ai_check') {
      const p = String(config.prompt ?? '');
      return p.length > 40 ? p.slice(0, 37) + '…' : p || 'prompt AI';
    }
    if (type === 'condition_group') {
      const cl = config.clauses;
      const n = Array.isArray(cl) ? cl.length : 0;
      return `${n} condizioni (${config.operator === 'or' ? 'O' : 'E'})`;
    }
  }
  if (kind === 'action') {
    if (type === 'send') {
      const parts: string[] = [];
      if (config.free_text) parts.push('testo');
      if (config.template_id) parts.push('template');
      return parts.length ? parts.join(' + ') : String(config.window_policy ?? 'auto');
    }
    if (type === 'ai_reply') return String(config.objective || 'AI');
    if (type === 'set_lead_field')
      return `${config.field ?? ''}${config.value ? ': ' + String(config.value) : ''}`;
    if (type === 'human_handoff') return String(config.reason || 'operatore umano');
    if (type === 'notify_slack') return config.text ? String(config.text) : 'Slack';
    if (type === 'send_message') return String(config.text ?? '');
    if (type === 'wait') {
      const u = config.unit === 'hours' ? 'ore' : config.unit === 'days' ? 'giorni' : 'min';
      return `${config.minutes ?? 0} ${u}`;
    }
    if (type === 'wait_until_before') return `${config.hours ?? 0}h prima dell'appuntamento`;
    if (type === 'send_template') return config.template_id ? 'template selezionato' : 'nessun template';
  }
  return '';
}

export interface AutomationNodeData {
  kind: NodeKind;
  type: string;
  label: string;
  config: Record<string, unknown>;
  [key: string]: unknown;
}

const SHELL =
  'min-w-[180px] max-w-[230px] rounded-lg border bg-card px-3 py-2 shadow-sm text-card-foreground transition';

function NodeChrome({
  selected,
  accent,
  icon,
  kindLabel,
  data,
}: {
  selected?: boolean;
  accent: string;
  icon: React.ReactNode;
  kindLabel: string;
  data: AutomationNodeData;
}) {
  const summary = nodeSummary(data.kind, data.type, data.config || {});
  return (
    <div className={`${SHELL} ${selected ? 'ring-2 ring-primary' : ''}`} style={{ borderColor: accent }}>
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide" style={{ color: accent }}>
        {icon}
        {kindLabel}
      </div>
      <div className="mt-0.5 text-sm font-medium leading-tight">{data.label}</div>
      {summary ? <div className="mt-0.5 truncate text-xs text-muted-foreground">{summary}</div> : null}
    </div>
  );
}

export function TriggerNode({ data, selected }: NodeProps) {
  const d = data as AutomationNodeData;
  return (
    <>
      <NodeChrome selected={selected} accent="#16a34a" icon={<Bolt size={12} />} kindLabel="Trigger" data={d} />
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}

export function ConditionNode({ data, selected }: NodeProps) {
  const d = data as AutomationNodeData;
  return (
    <>
      <Handle type="target" position={Position.Top} />
      <NodeChrome selected={selected} accent="#d97706" icon={<GitBranch size={12} />} kindLabel="Condizione" data={d} />
      <Handle type="source" position={Position.Bottom} id="true" style={{ left: '28%' }} />
      <Handle type="source" position={Position.Bottom} id="false" style={{ left: '72%' }} />
      <div className="pointer-events-none flex justify-between px-1 text-[9px] text-muted-foreground">
        <span>sì</span>
        <span>no</span>
      </div>
    </>
  );
}

export function ActionNode({ data, selected }: NodeProps) {
  const d = data as AutomationNodeData;
  return (
    <>
      <Handle type="target" position={Position.Top} />
      <NodeChrome selected={selected} accent="#2563eb" icon={<Send size={12} />} kindLabel="Azione" data={d} />
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}

export const automationNodeTypes = {
  trigger: TriggerNode,
  condition: ConditionNode,
  action: ActionNode,
};
