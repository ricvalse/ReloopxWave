/**
 * The config catalogue: which keys the merchant can tune, how they render, and
 * how they are grouped.
 *
 * Data only — extracted from the panel so the panel is about layout and state.
 * Section titles are what the merchant reads in the index, so they say what the
 * setting does ("Punteggio dei lead") rather than which use case it came from
 * ("Scoring (UC-05)"): the UC numbers are ours, not theirs.
 */

export type FieldKind =
  | 'int'
  | 'float'
  | 'text'
  | 'bool'
  | 'textarea'
  | 'select'
  | 'tags'
  | 'multiselect'
  | 'calendar';

/** Controls that need room go under their label; compact ones sit beside it. */
export const STACKED_KINDS = new Set<FieldKind>(['textarea', 'tags', 'multiselect']);

// Azioni che il bot può eseguire (ActionKind del backend). Usate dal
// multiselect "Azioni permesse" del playbook. "none" è sempre implicitamente
// permessa lato backend, quindi non compare qui.
const ACTION_OPTIONS: { value: string; label: string }[] = [
  { value: 'check_availability', label: 'Verifica disponibilità' },
  { value: 'lookup_appointment', label: 'Cerca appuntamento' },
  { value: 'propose_slots', label: 'Proponi disponibilità' },
  { value: 'book_slot', label: 'Prenota appuntamento' },
  { value: 'reschedule_slot', label: 'Sposta appuntamento' },
  { value: 'cancel_slot', label: 'Annulla appuntamento' },
  { value: 'move_pipeline', label: 'Avanza in pipeline' },
  { value: 'update_score', label: 'Aggiorna scoring' },
  { value: 'escalate_human', label: 'Passa a operatore' },
];

export type BadgeKind = 'inherited' | 'customized' | 'locked' | 'lock-override';

export type FieldDef = {
  key: string; // dotted path, e.g. "no_answer.first_reminder_min"
  label: string;
  kind: FieldKind;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  help?: string;
  rows?: number;
  options?: { value: string; label: string }[]; // for kind: 'select'
};

export type SectionDef = {
  section: string; // top-level key, e.g. "no_answer"
  title: string;
  /** Short label for the sticky index, where the full title would wrap. */
  nav: string;
  description: string;
  fields: FieldDef[];
};

export const SECTIONS: SectionDef[] = [
  {
    section: 'business',
    title: 'Profilo attività',
    nav: 'Attività',
    description:
      'Il bot risponde a nome dell’attività. Più sono completi questi campi, più pertinenti saranno le risposte.',
    fields: [
      { key: 'business.name', label: 'Nome attività', kind: 'text', placeholder: 'es. Studio Dentistico Rossi' },
      { key: 'business.industry', label: 'Settore', kind: 'text', placeholder: 'es. dentistico, consulenza, e-commerce' },
      {
        key: 'business.description',
        label: 'Descrizione breve',
        kind: 'textarea',
        rows: 3,
        placeholder: 'Chi siete, cosa offrite, cosa vi distingue.',
      },
      {
        key: 'business.offer',
        label: 'Offerta principale',
        kind: 'textarea',
        rows: 3,
        placeholder: 'Prodotti / servizi principali che il bot deve proporre.',
      },
      { key: 'business.hours', label: 'Orari', kind: 'text', placeholder: 'Lun-Ven 9:00-19:00' },
      { key: 'business.location', label: 'Sede / copertura', kind: 'text', placeholder: 'es. Milano, online in tutta Italia' },
      {
        key: 'business.pricing_notes',
        label: 'Note sui prezzi',
        kind: 'textarea',
        rows: 2,
        placeholder: 'Come parlare di prezzi; es. “a partire da 50€, preventivo su misura”.',
      },
      { key: 'business.website', label: 'Sito web', kind: 'text', placeholder: 'https://…' },
    ],
  },

  {
    section: 'conversation',
    title: 'Obiettivo e modalità',
    nav: 'Obiettivo',
    description:
      'Definisce cosa deve fare il bot. “Vendita” è il comportamento predefinito (qualifica → offerta → prenotazione). Scegli “Solo direttive” per un bot informativo/promemoria che NON qualifica e NON fa domande da colloquio: seguirà solo le regole che scrivi qui sotto.',
    fields: [
      {
        key: 'conversation.playbook.mode',
        label: 'Modalità',
        kind: 'select',
        options: [
          { value: 'fsm_legacy', label: 'Vendita (qualifica → offerta → prenotazione)' },
          { value: 'off', label: 'Solo direttive (nessuna qualifica, guidato dalle regole)' },
        ],
        help: '“Solo direttive” disattiva gli hint di vendita per-turno: il bot segue solo l’obiettivo e le regole qui sotto.',
      },
      {
        key: 'conversation.playbook.goal',
        label: 'Obiettivo',
        kind: 'textarea',
        rows: 2,
        placeholder:
          'es. Ricordare al candidato di compilare il questionario e dare SOLO info su procedura e step della selezione.',
      },
      {
        key: 'conversation.playbook.directives',
        label: 'Regole della conversazione',
        kind: 'tags',
        rows: 5,
        help: 'Regole vincolanti (hanno priorità su tutto), una per riga. Es. “Non fare mai domande da colloquio”, “Quando il candidato dice di aver inviato il questionario, rispondi «Ok grazie, perfetto» e chiudi”.',
      },
      {
        key: 'conversation.playbook.actions.enabled',
        label: 'Azioni permesse',
        kind: 'multiselect',
        options: ACTION_OPTIONS,
        help: 'Cosa può fare il bot oltre a rispondere. Nessuna selezione = tutte permesse (comportamento vendita). Per un bot informativo lascia solo “Passa a operatore”.',
      },
      {
        key: 'lead_capture.enabled',
        label: 'Raccogli dati del contatto (nome / email / esigenza)',
        kind: 'bool',
        help: 'Disattiva per un bot che non deve chiedere dati né intervistare il contatto.',
      },
      { key: 'scoring.enabled', label: 'Scoring del lead attivo', kind: 'bool' },
      {
        key: 'pipeline.auto_advance',
        label: 'Avanzamento automatico in pipeline',
        kind: 'bool',
        help: 'Quando attivo, il bot promuove il lead nella pipeline CRM al superamento della soglia.',
      },
      {
        key: 'booking.enabled',
        label: 'Prenotazioni attive',
        kind: 'bool',
        help: 'Quando disattivo, il bot non propone né gestisce appuntamenti.',
      },
    ],
  },
  {
    section: 'scoring',
    title: 'Punteggio dei lead',
    nav: 'Punteggio',
    description: 'Soglie per classificare hot / cold (quando lo scoring è attivo).',
    fields: [
      { key: 'scoring.hot_threshold', label: 'Hot threshold', kind: 'int', min: 50, max: 100 },
      { key: 'scoring.cold_threshold', label: 'Cold threshold', kind: 'int', min: 0, max: 50 },
    ],
  },
  {
    section: 'rag',
    title: 'Ricerca nella knowledge base',
    nav: 'Knowledge base',
    description: 'Retrieval dalla knowledge base.',
    fields: [
      { key: 'rag.top_k', label: 'Top K', kind: 'int', min: 3, max: 10 },
      { key: 'rag.min_score', label: 'Soglia minima', kind: 'float', min: 0.5, max: 0.9, step: 0.05 },
    ],
  },
  {
    section: 'pipeline',
    title: 'Pipeline CRM',
    nav: 'Pipeline',
    description:
      'Quando il bot promuove un lead. Il pipeline + new stage servono al booking per creare l’opportunità in GHL; il qualified stage è dove il bot la sposta quando il lead si qualifica.',
    fields: [
      { key: 'pipeline.advance_threshold', label: 'Soglia avanzamento', kind: 'int', min: 0, max: 100 },
      { key: 'pipeline.default_pipeline_id', label: 'GHL pipeline ID (default)', kind: 'text' },
      { key: 'pipeline.new_stage_id', label: 'GHL new-lead stage ID', kind: 'text' },
      { key: 'pipeline.qualified_stage_id', label: 'GHL qualified stage ID', kind: 'text' },
    ],
  },
  {
    section: 'booking',
    title: 'Prenotazioni',
    nav: 'Prenotazioni',
    description: 'Default calendario e durata appuntamenti.',
    fields: [
      { key: 'booking.default_duration_min', label: 'Durata default (min)', kind: 'int', min: 15, max: 240 },
      { key: 'booking.lookahead_days', label: 'Lookahead (giorni)', kind: 'int', min: 1, max: 60 },
      {
        key: 'booking.default_calendar_id',
        label: 'Calendario default',
        kind: 'calendar',
        help: 'Calendario GHL su cui il bot prenota. Se GHL non è collegato, inserisci l’ID manualmente.',
      },
    ],
  },
  {
    section: 'schedule',
    title: 'Orari di risposta',
    nav: 'Orari',
    description: 'Orari attivi, messaggio fuori orario, timezone.',
    fields: [
      { key: 'schedule.active_hours', label: 'Orari attivi', kind: 'text' },
      { key: 'schedule.off_hours_message', label: 'Messaggio fuori orario', kind: 'text' },
      { key: 'schedule.timezone', label: 'Timezone', kind: 'text' },
    ],
  },
  {
    section: 'bot',
    title: 'Persona del bot',
    nav: 'Persona',
    description:
      'Come parla il bot: registro, lunghezza, emoji, saluti e frasi tipiche. Questi controlli guidati compongono il prompt di sistema.',
    fields: [
      {
        key: 'bot.auto_reply_enabled',
        label: 'Risposta automatica',
        kind: 'bool',
        help:
          'Quando attivo, il bot risponde automaticamente ai messaggi in arrivo. Disattivandolo metti in pausa il bot per tutti i contatti — i messaggi resteranno in attesa di una tua risposta dal pannello Conversazioni.',
      },
      { key: 'bot.language', label: 'Lingua', kind: 'text', placeholder: 'it' },
      {
        key: 'bot.formality',
        label: 'Come rivolgersi al cliente',
        kind: 'select',
        options: [
          { value: 'auto', label: 'Automatico (usa il tono)' },
          { value: 'dai-del-tu', label: 'Dai del tu' },
          { value: 'dai-del-lei', label: 'Dai del Lei' },
        ],
        help: 'Su “Automatico” usa il campo Tono (sezione Avanzate).',
      },
      {
        key: 'bot.verbosity',
        label: 'Lunghezza risposte',
        kind: 'select',
        options: [
          { value: 'conciso', label: 'Conciso' },
          { value: 'equilibrato', label: 'Equilibrato' },
          { value: 'dettagliato', label: 'Dettagliato' },
        ],
      },
      {
        key: 'bot.emoji_policy',
        label: 'Emoji',
        kind: 'select',
        options: [
          { value: 'mai', label: 'Mai' },
          { value: 'sobrio', label: 'Sobrio' },
          { value: 'libero', label: 'Libero' },
        ],
      },
      {
        key: 'bot.greeting_style',
        label: 'Stile di apertura',
        kind: 'text',
        placeholder: 'es. saluta col nome se disponibile',
      },
      {
        key: 'bot.signature',
        label: 'Firma',
        kind: 'text',
        placeholder: 'es. — Il team di Studio Rossi',
      },
      {
        key: 'bot.do_phrases',
        label: 'Espressioni da preferire',
        kind: 'tags',
        rows: 3,
        help: 'Una per riga.',
      },
      {
        key: 'bot.dont_phrases',
        label: 'Espressioni / toni da evitare',
        kind: 'tags',
        rows: 3,
        help: 'Una per riga.',
      },
      {
        key: 'bot.sentiment_adaptation_enabled',
        label: 'Adatta il tono al sentiment',
        kind: 'bool',
        help:
          'Se il cliente sembrava insoddisfatto nel messaggio precedente, il bot apre con empatia; se ben disposto, propone il passo successivo.',
      },
      {
        key: 'bot.first_message',
        label: 'Messaggio di benvenuto',
        kind: 'textarea',
        rows: 2,
        placeholder: 'Primo messaggio quando scriviamo a un nuovo lead.',
      },
    ],
  },
  {
    section: 'bot_advanced',
    title: 'Impostazioni avanzate',
    nav: 'Avanzate',
    description:
      'Controlli liberi per casi particolari. Il “Tono” è usato solo quando “Come rivolgersi al cliente” è su Automatico; le istruzioni aggiuntive hanno priorità sul resto.',
    fields: [
      { key: 'bot.tone', label: 'Tono (libero)', kind: 'text', placeholder: 'professionale-amichevole' },
      {
        key: 'bot.system_prompt_additions',
        label: 'Istruzioni aggiuntive',
        kind: 'textarea',
        rows: 4,
        placeholder:
          'Regole aggiuntive che vuoi dare al bot (stile, argomenti da evitare, script particolari).',
      },
    ],
  },
  {
    section: 'delivery',
    title: 'Consegna dei messaggi',
    nav: 'Consegna',
    description:
      'Fa sembrare le risposte più umane su WhatsApp. Attivo di default (debounce, indicatore “sta scrivendo…”, breve pausa, più bolle): regola o azzera ciò che vuoi (0/disattivo = invio immediato in un solo messaggio). La finestra raggruppa messaggi ravvicinati in un’unica risposta.',
    fields: [
      {
        key: 'delivery.debounce_window_s',
        label: 'Finestra di attesa (s)',
        kind: 'int',
        min: 0,
        max: 30,
        help: '0 = risposta immediata. Es. 5 = aspetta 5s di silenzio prima di rispondere, unendo i messaggi.',
      },
      {
        key: 'delivery.typing_indicator_enabled',
        label: 'Mostra “sta scrivendo…”',
        kind: 'bool',
      },
      {
        key: 'delivery.typing_delay_max_s',
        label: 'Ritardo max “digitazione” (s)',
        kind: 'float',
        min: 0,
        max: 20,
        step: 0.5,
        help: '0 = nessun ritardo. Tetto al tempo di “digitazione” simulato.',
      },
      {
        key: 'delivery.typing_delay_base_s',
        label: 'Ritardo base (s)',
        kind: 'float',
        min: 0,
        max: 10,
        step: 0.1,
      },
      {
        key: 'delivery.typing_delay_per_char_s',
        label: 'Ritardo per carattere (s)',
        kind: 'float',
        min: 0,
        max: 0.2,
        step: 0.01,
      },
      {
        key: 'delivery.typing_delay_min_s',
        label: 'Ritardo minimo (s)',
        kind: 'float',
        min: 0,
        max: 20,
        step: 0.5,
      },
      {
        key: 'delivery.typing_jitter_frac',
        label: 'Variabilità (0–1)',
        kind: 'float',
        min: 0,
        max: 1,
        step: 0.05,
      },
      {
        key: 'delivery.multi_bubble_max',
        label: 'Max bolle per risposta',
        kind: 'int',
        min: 1,
        max: 4,
        help: '1 = una sola bolla. >1 spezza le risposte lunghe come farebbe una persona.',
      },
      {
        key: 'delivery.bubble_max_chars',
        label: 'Caratteri max per bolla',
        kind: 'int',
        min: 20,
        max: 1000,
        help: 'Sotto questa soglia la risposta resta un messaggio unico. Valori bassi (20–50) = circa una frase per bolla; le frasi non vengono mai spezzate a metà.',
      },
    ],
  },
  {
    section: 'escalation',
    title: 'Passaggio a operatore',
    nav: 'Operatore',
    description: 'Quando passare la chat a un operatore umano.',
    fields: [
      { key: 'escalation.enabled', label: 'Abilitata', kind: 'bool' },
      {
        key: 'escalation.handoff_message',
        label: 'Messaggio di passaggio',
        kind: 'textarea',
        placeholder: 'es. “Ti metto subito in contatto con un nostro operatore.” (vuoto = lascia scrivere al bot)',
        help: 'Vuoto: il messaggio lo scrive il bot. Ignorato se il passaggio silenzioso è attivo.',
      },
      {
        key: 'escalation.silent_handoff',
        label: 'Passaggio silenzioso (nessun messaggio al cliente)',
        kind: 'bool',
        help: 'Il cliente non riceve nulla e il bot esce dalla chat. L’operatore viene comunque avvisato (es. su Slack).',
      },
      {
        key: 'escalation.critical_keywords',
        label: 'Parole chiave critiche',
        kind: 'tags',
        rows: 3,
        help: 'Parole che forzano il passaggio al modello di escalation, una per riga. Vuoto = vocabolario predefinito. Utile se una parola predefinita (es. “concorrenza”) è normale nel tuo settore.',
      },
    ],
  },
  {
    section: 'privacy',
    title: 'Privacy',
    nav: 'Privacy',
    description: 'Retention dati conversazioni.',
    fields: [
      { key: 'privacy.retention_months', label: 'Retention (mesi)', kind: 'int', min: 6, max: 60 },
    ],
  },
  {
    section: 'ab_test',
    title: 'A/B testing',
    nav: 'A/B testing',
    description: 'Defaults sperimentazione.',
    fields: [
      { key: 'ab_test.min_sample', label: 'Min sample size', kind: 'int', min: 50, max: 1000 },
    ],
  },
];

// Sezioni nascoste dal pannello Configurazione. Le definizioni restano in
// SECTIONS sopra: per riattivarne una basta togliere la chiave da questo set.
// Le sezioni operative (no_answer/reactivation/scoring/booking) sono ora
// esposte: erano il gap percepito più grande rispetto alla console di Amalia.
// Restano nascoste solo quelle gestite altrove o puramente tecniche.
export const HIDDEN_SECTIONS = new Set<string>([
  'business', // Spostato nella pagina dedicata "Brand → Informazioni"
  'rag', // RAG (UC-07) — parametri tecnici, gestiti dalla Knowledge Base
  'pipeline', // Pipeline (UC-04) — richiede gli ID GHL, gestiti dalle Integrazioni
]);

export const VISIBLE_SECTIONS = SECTIONS.filter((s) => !HIDDEN_SECTIONS.has(s.section));
