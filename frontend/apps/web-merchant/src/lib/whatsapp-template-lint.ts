/**
 * Validazione client-side dei template WhatsApp + linee guida mostrate nel form.
 *
 * Porta in TypeScript le regole che Meta applica PRIMA della submission e che il
 * backend verifica in `integrations/whatsapp/templates.py` (`lint_template`).
 * Anticiparle nel browser evita il classico rifiuto "Invalid Format" che
 * altrimenti arriverebbe via webhook ore dopo l'invio. Il backend resta
 * l'autorità: qui replichiamo le regole deterministiche per la UX dal vivo.
 *
 * `error`   → blocca l'invio (regola deterministica, allineata al backend).
 * `warning` → consiglio non bloccante (euristica / rischio di riclassificazione).
 */

// Limiti per le regole Meta / 360dialog (allineati a templates.py).
export const MAX_BODY_LEN = 1024;
export const MAX_FOOTER_LEN = 60;
export const MAX_HEADER_TEXT_LEN = 60;
export const MAX_VARIABLES = 10;
export const MAX_BUTTON_TEXT_LEN = 25;
export const MAX_BUTTONS_TOTAL = 10;
export const MAX_URL_BUTTONS = 2;
export const MAX_PHONE_BUTTONS = 1;
export const MAX_COPY_CODE_BUTTONS = 1;
export const VALID_CATEGORIES = ['MARKETING', 'UTILITY', 'AUTHENTICATION'] as const;
export const VALID_BUTTON_TYPES = ['QUICK_REPLY', 'URL', 'PHONE_NUMBER', 'COPY_CODE'] as const;

// Sottoinsieme dei codici lingua WhatsApp (allineato a SUPPORTED_LANGUAGES backend).
export const SUPPORTED_LANGUAGES = new Set<string>([
  'af', 'sq', 'ar', 'az', 'bn', 'bg', 'ca', 'zh_CN', 'zh_HK', 'zh_TW', 'hr', 'cs',
  'da', 'nl', 'en', 'en_GB', 'en_US', 'et', 'fil', 'fi', 'fr', 'ka', 'de', 'el',
  'gu', 'ha', 'he', 'hi', 'hu', 'id', 'ga', 'it', 'ja', 'kn', 'kk', 'rw_RW', 'ko',
  'ky_KG', 'lo', 'lv', 'lt', 'mk', 'ms', 'ml', 'mr', 'nb', 'fa', 'pl', 'pt_BR',
  'pt_PT', 'pa', 'ro', 'ru', 'sr', 'sk', 'sl', 'es', 'es_AR', 'es_ES', 'es_MX',
  'sw', 'sv', 'ta', 'te', 'th', 'tr', 'uk', 'ur', 'uz', 'vi', 'zu',
]);

const PROMO_KEYWORDS = [
  'sconto', 'offerta', 'promo', 'promozione', 'gratis', 'saldi', 'buono', 'coupon',
  'regalo', 'omaggio', 'black friday', 'occasione', 'affare', 'imperdibile',
  'acquista ora', 'compra ora', 'discount', 'sale', 'free', '% off', '% di sconto',
];

const VAR_RE = /\{\{\s*(\d+)\s*\}\}/g;
const VAR_AT_START_RE = /^\{\{\s*\d+\s*\}\}/;
const VAR_AT_END_RE = /\{\{\s*\d+\s*\}\}$/;
const VAR_ADJACENT_RE = /\{\{\s*\d+\s*\}\}\s*\{\{\s*\d+\s*\}\}/;
const LANG_RE = /^[a-z]{2,3}(_[A-Z]{2})?$/;
const URL_RE = /https?:\/\//i;
const EMOJI_RE = /\p{Extended_Pictographic}/u;

export type LintLevel = 'error' | 'warning';
export type LintField = 'body' | 'footer' | 'header' | 'category' | 'language' | 'buttons';

export interface LintIssue {
  code: string;
  level: LintLevel;
  field: LintField;
  message: string;
}

export interface TemplateButtonInput {
  type: string; // QUICK_REPLY | URL | PHONE_NUMBER | COPY_CODE
  text?: string;
  url?: string;
  phone_number?: string;
}

export interface LintInput {
  body: string;
  category?: string;
  language?: string;
  footer?: string | null;
  headerType?: string;
  headerText?: string | null;
  buttons?: TemplateButtonInput[];
  bodyExamples?: string[];
}

/** Numeri dei placeholder `{{n}}` nell'ordine di apparizione, de-duplicati. */
export function extractVariables(text: string): number[] {
  const seen: number[] = [];
  for (const match of (text || '').matchAll(VAR_RE)) {
    const num = Number(match[1]);
    if (!seen.includes(num)) seen.push(num);
  }
  return seen;
}

function lintLanguage(language: string): LintIssue[] {
  const lang = (language || '').trim();
  if (!lang) {
    return [{ code: 'LANG_REQUIRED', level: 'error', field: 'language', message: 'La lingua è obbligatoria.' }];
  }
  if (!LANG_RE.test(lang)) {
    return [
      {
        code: 'LANG_FORMAT',
        level: 'error',
        field: 'language',
        message: 'Codice lingua non valido: usa il formato Meta, es. it, en, en_US.',
      },
    ];
  }
  if (!SUPPORTED_LANGUAGES.has(lang)) {
    return [
      {
        code: 'LANG_UNSUPPORTED',
        level: 'warning',
        field: 'language',
        message: `«${lang}» non è un codice lingua WhatsApp riconosciuto — controllalo.`,
      },
    ];
  }
  return [];
}

function lintVariables(body: string): LintIssue[] {
  const issues: LintIssue[] = [];
  const nums = extractVariables(body);
  if (nums.length === 0) return issues;

  if (nums.length > MAX_VARIABLES) {
    issues.push({
      code: 'VAR_TOO_MANY',
      level: 'error',
      field: 'body',
      message: `Massimo ${MAX_VARIABLES} variabili per template.`,
    });
  }

  const expected = Array.from({ length: nums.length }, (_, i) => i + 1);
  const sorted = [...nums].sort((a, b) => a - b);
  if (sorted.join(',') !== expected.join(',')) {
    issues.push({
      code: 'VAR_NON_SEQUENTIAL',
      level: 'error',
      field: 'body',
      message:
        'Le variabili devono essere numerate in sequenza 1, 2, 3… senza saltare numeri (es. niente {{1}} e {{3}} senza {{2}}).',
    });
  }

  const stripped = body.trim();
  if (VAR_AT_START_RE.test(stripped)) {
    issues.push({
      code: 'VAR_AT_START',
      level: 'error',
      field: 'body',
      message: 'Il messaggio non può iniziare con una variabile: scrivi del testo prima di {{1}}.',
    });
  }
  if (VAR_AT_END_RE.test(stripped)) {
    issues.push({
      code: 'VAR_AT_END',
      level: 'error',
      field: 'body',
      message: 'Il messaggio non può finire con una variabile: aggiungi del testo dopo l’ultima {{…}}.',
    });
  }
  if (VAR_ADJACENT_RE.test(body)) {
    issues.push({
      code: 'VAR_ADJACENT',
      level: 'error',
      field: 'body',
      message: 'Due variabili non possono stare una accanto all’altra: inserisci del testo tra {{1}} e {{2}}.',
    });
  }

  const staticWords = body.replace(VAR_RE, ' ').split(/\s+/).filter(Boolean).length;
  const minWords = 3 * nums.length + 1;
  if (staticWords < minWords) {
    issues.push({
      code: 'VAR_RATIO_LOW',
      level: 'warning',
      field: 'body',
      message: `Poco testo rispetto alle variabili: aggiungi parole fisse (consigliate almeno ~${minWords}) per ridurre il rischio di rifiuto.`,
    });
  }

  return issues;
}

function lintBodyFormatting(body: string): LintIssue[] {
  const issues: LintIssue[] = [];
  // Allineato al backend: tab e run di spazi/righe sono ERRORI (Meta li rifiuta).
  if (/\t/.test(body)) {
    issues.push({
      code: 'BODY_TAB',
      level: 'error',
      field: 'body',
      message: 'Niente caratteri di tabulazione (TAB) nel corpo del messaggio.',
    });
  }
  if (/ {5,}/.test(body)) {
    issues.push({
      code: 'BODY_SPACE_RUN',
      level: 'error',
      field: 'body',
      message: 'Niente più di 4 spazi consecutivi nel corpo del messaggio.',
    });
  }
  if (/\n{5,}/.test(body)) {
    issues.push({
      code: 'BODY_NEWLINE_RUN',
      level: 'error',
      field: 'body',
      message: 'Niente più di 4 interruzioni di riga consecutive.',
    });
  }
  if (/\*\*|__(?!_)|^#{1,6}\s/m.test(body)) {
    issues.push({
      code: 'BODY_MARKDOWN',
      level: 'warning',
      field: 'body',
      message:
        'Formattazione non valida: WhatsApp usa *grassetto*, _corsivo_, ~barrato~ (un solo simbolo). Niente **doppi asterischi** o titoli #.',
    });
  }
  return issues;
}

function lintExamples(body: string, bodyExamples?: string[]): LintIssue[] {
  if (bodyExamples === undefined) return [];
  const varCount = extractVariables(body).length;
  if (varCount === 0) return [];
  const provided = bodyExamples.filter((e) => e && e.trim()).length;
  if (provided < varCount) {
    return [
      {
        code: 'VAR_EXAMPLE_MISSING',
        level: 'warning',
        field: 'body',
        message: `Aggiungi un valore di esempio realistico per ognuna delle ${varCount} variabili: riduce il rischio di rifiuto.`,
      },
    ];
  }
  return [];
}

function lintButtons(buttons: TemplateButtonInput[]): LintIssue[] {
  const issues: LintIssue[] = [];
  if (!buttons.length) return issues;
  if (buttons.length > MAX_BUTTONS_TOTAL) {
    issues.push({
      code: 'BUTTONS_TOO_MANY',
      level: 'error',
      field: 'buttons',
      message: `Massimo ${MAX_BUTTONS_TOTAL} pulsanti per template.`,
    });
  }
  const counts: Record<string, number> = {};
  buttons.forEach((btn, i) => {
    const type = String(btn.type || '').toUpperCase();
    if (!VALID_BUTTON_TYPES.includes(type as (typeof VALID_BUTTON_TYPES)[number])) {
      issues.push({ code: 'BUTTON_TYPE_INVALID', level: 'error', field: 'buttons', message: `Pulsante ${i + 1}: tipo non valido.` });
      return;
    }
    counts[type] = (counts[type] ?? 0) + 1;
    const text = String(btn.text ?? '');
    if (type !== 'COPY_CODE' && !text.trim()) {
      issues.push({ code: 'BUTTON_TEXT_REQUIRED', level: 'error', field: 'buttons', message: `Pulsante ${i + 1}: serve un'etichetta.` });
    }
    if (text.length > MAX_BUTTON_TEXT_LEN) {
      issues.push({
        code: 'BUTTON_TEXT_TOO_LONG',
        level: 'error',
        field: 'buttons',
        message: `Pulsante ${i + 1}: l'etichetta supera i ${MAX_BUTTON_TEXT_LEN} caratteri.`,
      });
    }
    if (type === 'URL') {
      const url = String(btn.url ?? '');
      if (!url) {
        issues.push({ code: 'BUTTON_URL_REQUIRED', level: 'error', field: 'buttons', message: `Pulsante ${i + 1}: serve un URL.` });
      } else {
        if (!url.toLowerCase().startsWith('https://')) {
          issues.push({ code: 'BUTTON_URL_NOT_HTTPS', level: 'error', field: 'buttons', message: `Pulsante ${i + 1}: l'URL deve iniziare con https://.` });
        }
        if ((url.match(VAR_RE) ?? []).length > 1) {
          issues.push({ code: 'BUTTON_URL_VARS', level: 'error', field: 'buttons', message: `Pulsante ${i + 1}: l'URL può contenere al massimo una variabile.` });
        }
      }
    }
    if (type === 'PHONE_NUMBER') {
      const phone = String(btn.phone_number ?? '').trim();
      if (!phone) {
        issues.push({ code: 'BUTTON_PHONE_REQUIRED', level: 'error', field: 'buttons', message: `Pulsante ${i + 1}: serve un numero di telefono.` });
      } else if (!phone.startsWith('+') || phone.length > 20) {
        issues.push({ code: 'BUTTON_PHONE_FORMAT', level: 'error', field: 'buttons', message: `Pulsante ${i + 1}: numero in formato internazionale +… (max 20 caratteri).` });
      }
    }
  });
  if ((counts.URL ?? 0) > MAX_URL_BUTTONS) {
    issues.push({ code: 'BUTTONS_URL_TOO_MANY', level: 'error', field: 'buttons', message: `Massimo ${MAX_URL_BUTTONS} pulsanti URL.` });
  }
  if ((counts.PHONE_NUMBER ?? 0) > MAX_PHONE_BUTTONS) {
    issues.push({ code: 'BUTTONS_PHONE_TOO_MANY', level: 'error', field: 'buttons', message: `Massimo ${MAX_PHONE_BUTTONS} pulsante telefono.` });
  }
  if ((counts.COPY_CODE ?? 0) > MAX_COPY_CODE_BUTTONS) {
    issues.push({ code: 'BUTTONS_COPY_TOO_MANY', level: 'error', field: 'buttons', message: `Massimo ${MAX_COPY_CODE_BUTTONS} pulsante copia-codice.` });
  }
  return issues;
}

function lintCategorySemantics(body: string, footer: string | null | undefined, category: string): LintIssue[] {
  if (category !== 'UTILITY') return [];
  const text = `${body} ${footer ?? ''}`.toLowerCase();
  if (PROMO_KEYWORDS.some((kw) => text.includes(kw))) {
    return [
      {
        code: 'CAT_PROMO_IN_UTILITY',
        level: 'warning',
        field: 'category',
        message: 'Linguaggio promozionale in un template UTILITY: spesso viene riclassificato o rifiutato — valuta MARKETING.',
      },
    ];
  }
  return [];
}

function lintAuthentication(category: string, body: string): LintIssue[] {
  if (category !== 'AUTHENTICATION') return [];
  const issues: LintIssue[] = [];
  if (URL_RE.test(body)) {
    issues.push({ code: 'AUTH_NO_URL', level: 'error', field: 'body', message: 'I template AUTHENTICATION non possono contenere link.' });
  }
  if (EMOJI_RE.test(body)) {
    issues.push({ code: 'AUTH_NO_EMOJI', level: 'warning', field: 'body', message: 'I template AUTHENTICATION non possono contenere emoji.' });
  }
  return issues;
}

/** Verifica un template e ritorna gli avvisi/errori (lista vuota = valido). */
export function lintTemplate(input: LintInput): LintIssue[] {
  const {
    body,
    category = 'UTILITY',
    language = 'it',
    footer,
    headerType = 'NONE',
    headerText,
    buttons = [],
    bodyExamples,
  } = input;
  const issues: LintIssue[] = [];

  if (!VALID_CATEGORIES.includes(category as (typeof VALID_CATEGORIES)[number])) {
    issues.push({
      code: 'CATEGORY_INVALID',
      level: 'error',
      field: 'category',
      message: `La categoria deve essere una tra ${VALID_CATEGORIES.join(', ')}.`,
    });
  }

  issues.push(...lintLanguage(language));

  if (!body || !body.trim()) {
    issues.push({ code: 'BODY_EMPTY', level: 'error', field: 'body', message: 'Il corpo del messaggio è obbligatorio.' });
  } else if (body.length > MAX_BODY_LEN) {
    issues.push({
      code: 'BODY_TOO_LONG',
      level: 'error',
      field: 'body',
      message: `Il corpo supera i ${MAX_BODY_LEN} caratteri (attuali: ${body.length}).`,
    });
  }

  issues.push(...lintVariables(body || ''));
  if (body && body.trim()) issues.push(...lintBodyFormatting(body));
  issues.push(...lintExamples(body || '', bodyExamples));

  if (headerType === 'TEXT') {
    if (!headerText || !headerText.trim()) {
      issues.push({ code: 'HEADER_TEXT_REQUIRED', level: 'error', field: 'header', message: 'L’intestazione di testo richiede un contenuto.' });
    } else {
      if (headerText.length > MAX_HEADER_TEXT_LEN) {
        issues.push({ code: 'HEADER_TOO_LONG', level: 'error', field: 'header', message: `L’intestazione supera i ${MAX_HEADER_TEXT_LEN} caratteri.` });
      }
      if (extractVariables(headerText).length > 0) {
        issues.push({ code: 'HEADER_HAS_VARIABLE', level: 'error', field: 'header', message: 'L’intestazione non può contenere variabili.' });
      }
    }
  }

  if (footer != null && footer !== '') {
    if (footer.length > MAX_FOOTER_LEN) {
      issues.push({ code: 'FOOTER_TOO_LONG', level: 'error', field: 'footer', message: `Il footer supera i ${MAX_FOOTER_LEN} caratteri (attuali: ${footer.length}).` });
    }
    if (extractVariables(footer).length > 0) {
      issues.push({ code: 'FOOTER_HAS_VARIABLE', level: 'error', field: 'footer', message: 'Il footer non può contenere variabili {{…}}.' });
    }
    if (EMOJI_RE.test(footer)) {
      issues.push({ code: 'FOOTER_EMOJI', level: 'warning', field: 'footer', message: 'Evita emoji nel footer: usa solo testo semplice.' });
    }
  }

  issues.push(...lintButtons(buttons));
  issues.push(...lintCategorySemantics(body || '', footer, category));
  issues.push(...lintAuthentication(category, body || ''));

  return issues;
}

export interface GuidelineSection {
  title: string;
  items: string[];
}

/** Linee guida pratiche (in italiano) mostrate nel form per evitare i rifiuti Meta. */
export const TEMPLATE_GUIDELINES: GuidelineSection[] = [
  {
    title: 'Variabili {{1}}, {{2}}…',
    items: [
      'Scrivi sempre del testo prima e dopo ogni variabile: il messaggio non può iniziare né finire con {{…}}.',
      'Non mettere due variabili una accanto all’altra: inserisci parole tra di esse (es. «ordine {{1}} del {{2}}»).',
      'Numera in ordine senza saltare numeri: {{1}}, {{2}}, {{3}}… Se elimini una variabile, rinumera le altre.',
      'Compila un valore di esempio realistico per ogni variabile: Meta li usa in fase di revisione.',
    ],
  },
  {
    title: 'Testo, formattazione e pulsanti',
    items: [
      'Niente TAB, niente più di 4 spazi di fila, niente più di 4 a-capo consecutivi.',
      'Formattazione solo in stile WhatsApp e con i simboli chiusi: *grassetto*, _corsivo_, ~barrato~. Niente **doppi asterischi**, titoli # o HTML.',
      'Nel footer solo testo fisso: niente variabili, niente emoji.',
      'Pulsanti: max 10 totali, max 2 URL (solo https://), max 1 telefono, etichette ≤ 25 caratteri.',
    ],
  },
  {
    title: 'Categoria, lingua e limiti',
    items: [
      'Categoria giusta: UTILITY per notifiche transazionali (ordini, appuntamenti), MARKETING per promozioni/offerte, AUTHENTICATION solo per codici OTP. Contenuto promozionale in un template UTILITY viene rifiutato.',
      'Lingua in formato Meta (es. it, en, en_US) coerente con il testo scritto.',
      `Rispetta i limiti: corpo max ${MAX_BODY_LEN} caratteri, footer max ${MAX_FOOTER_LEN}, intestazione max ${MAX_HEADER_TEXT_LEN}.`,
      'Non duplicare un template già esistente con lo stesso testo e non inserire link a domini non tuoi o richieste di dati sensibili.',
    ],
  },
];
