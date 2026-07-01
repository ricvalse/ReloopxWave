'use client';

import { Fragment, type ReactNode } from 'react';
import type { TemplateButtonInput } from '@/lib/whatsapp-template-lint';

export interface TemplatePreviewProps {
  body: string;
  headerType?: string;
  headerText?: string | null;
  headerImageUrl?: string | null;
  footer?: string | null;
  buttons?: TemplateButtonInput[];
  examples?: string[];
}

const VAR_RE = /\{\{\s*(\d+)\s*\}\}/g;

/** Sostituisce {{n}} con l'esempio corrispondente (o un segnaposto «n»). */
function fillExamples(text: string, examples: string[]): string {
  return text.replace(VAR_RE, (_m, n) => {
    const idx = Number(n) - 1;
    const value = examples[idx];
    return value && value.trim() ? value : `{{${n}}}`;
  });
}

/**
 * Render minimale della formattazione WhatsApp: *grassetto*, _corsivo_,
 * ~barrato~, ```monospace```. Tokenizza senza dangerouslySetInnerHTML.
 */
function renderWhatsAppMarkup(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*[^*\n]+\*|_[^_\n]+_|~[^~\n]+~|```[^`]+```)/g;
  let lastIndex = 0;
  let key = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(<Fragment key={key++}>{text.slice(lastIndex, match.index)}</Fragment>);
    }
    const token = match[0];
    const inner = token.slice(token.startsWith('```') ? 3 : 1, token.startsWith('```') ? -3 : -1);
    if (token.startsWith('*')) nodes.push(<strong key={key++}>{inner}</strong>);
    else if (token.startsWith('_')) nodes.push(<em key={key++}>{inner}</em>);
    else if (token.startsWith('~')) nodes.push(<s key={key++}>{inner}</s>);
    else nodes.push(<code key={key++} className="rounded bg-black/10 px-1 font-mono text-[0.8em]">{inner}</code>);
    lastIndex = match.index + token.length;
  }
  if (lastIndex < text.length) {
    nodes.push(<Fragment key={key++}>{text.slice(lastIndex)}</Fragment>);
  }
  return nodes;
}

function renderMultiline(text: string): ReactNode {
  return text.split('\n').map((line, i) => (
    <Fragment key={i}>
      {i > 0 ? <br /> : null}
      {renderWhatsAppMarkup(line)}
    </Fragment>
  ));
}

const BUTTON_ICON: Record<string, string> = {
  URL: '🔗',
  PHONE_NUMBER: '📞',
  COPY_CODE: '⧉',
  QUICK_REPLY: '↩',
};

export function WhatsAppTemplatePreview({
  body,
  headerType = 'NONE',
  headerText,
  headerImageUrl,
  footer,
  buttons = [],
  examples = [],
}: TemplatePreviewProps) {
  const filledBody = fillExamples(body || '', examples);

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Anteprima WhatsApp
      </p>
      {/* Sfondo stile chat WhatsApp */}
      <div className="rounded-lg bg-[#e5ddd5] p-4 dark:bg-[#0b141a]">
        <div className="max-w-[85%] rounded-lg rounded-tl-sm bg-white px-3 py-2 text-sm shadow-sm dark:bg-[#202c33] dark:text-zinc-100">
          {headerType === 'TEXT' && headerText ? (
            <p className="mb-1 font-semibold">{renderMultiline(headerText)}</p>
          ) : null}
          {headerType === 'IMAGE' ? (
            headerImageUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={headerImageUrl}
                alt="Immagine intestazione"
                className="mb-2 max-h-40 w-full rounded object-cover"
              />
            ) : (
              <div className="mb-2 flex h-24 items-center justify-center rounded bg-black/10 text-xs text-muted-foreground">
                Immagine intestazione
              </div>
            )
          ) : null}

          {filledBody.trim() ? (
            <p className="whitespace-pre-wrap break-words leading-snug">{renderMultiline(filledBody)}</p>
          ) : (
            <p className="italic text-muted-foreground">Il corpo del messaggio apparirà qui…</p>
          )}

          {footer ? <p className="mt-1 text-xs text-muted-foreground">{footer}</p> : null}

          <p className="mt-1 text-right text-[10px] text-muted-foreground/70">12:00 ✓✓</p>
        </div>

        {buttons.length > 0 ? (
          <div className="mt-1 max-w-[85%] space-y-px overflow-hidden rounded-lg">
            {buttons.map((btn, i) => (
              <div
                key={i}
                className="bg-white px-3 py-2 text-center text-sm font-medium text-[#00a5f4] dark:bg-[#202c33]"
              >
                <span className="mr-1">{BUTTON_ICON[String(btn.type).toUpperCase()] ?? ''}</span>
                {btn.text?.trim() || (String(btn.type).toUpperCase() === 'COPY_CODE' ? 'Copia codice' : 'Pulsante')}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
