'use client';

import * as React from 'react';

/**
 * Il testo che si sta digitando da una parte, la lista normalizzata dall'altra.
 *
 * Un campo controllato che normalizza dentro il proprio `onChange` e rimanda il
 * risultato nel proprio `value` cancella quello che l'utente sta scrivendo:
 * `trim()` si mangia lo spazio appena battuto e `filter(Boolean)` si mangia la
 * riga vuota appena aperta con Invio. Il campo non reagisce, e chi lo usa
 * conclude — come è successo — che "non si possono inserire spazi e andare a
 * capo".
 *
 * Qui il testo grezzo vive in uno stato locale ed è l'unica cosa che il campo
 * mostra; la normalizzazione viaggia solo verso l'esterno, verso chi salva. Il
 * buffer si riallinea soltanto quando la lista cambia **da fuori**
 * (caricamento, reset del campo, preset di tono, cambio di merchant), mai per
 * effetto della normalizzazione di ciò che si sta scrivendo in questo momento.
 */
export function useListDraft({
  items,
  separator,
  join,
  onChange,
}: {
  /** Valore corrente, già normalizzato: una voce per elemento. */
  items: string[];
  /** Carattere su cui spezzare ciò che viene digitato (`'\n'`, `','`, …). */
  separator: string;
  /** Come le voci vengono ricomposte a schermo quando il valore arriva da fuori. */
  join: string;
  /** Riceve la lista normalizzata a ogni battuta. Mai il testo grezzo. */
  onChange: (items: string[]) => void;
}): { text: string; setText: (raw: string) => void } {
  const canonical = items.join(join);
  const [draft, setDraft] = React.useState(canonical);

  // L'ultima forma canonica che abbiamo emesso *noi*. È ciò che distingue il
  // valore di ritorno del nostro stesso onChange — da ignorare, perché il
  // buffer è più fedele di quanto abbiamo appena normalizzato — da una modifica
  // arrivata da fuori, che invece va recepita.
  const [emitted, setEmitted] = React.useState(canonical);

  // Aggiustamento in fase di render, non in un effetto: `useEffect` è passivo e
  // gira dopo il paint, quindi un "Ripristina" restava visibile per un
  // fotogramma con il testo vecchio. Così React ri-renderizza subito, prima di
  // disegnare.
  if (canonical !== emitted) {
    setEmitted(canonical);
    setDraft(canonical);
  }

  const setText = (raw: string) => {
    const next = splitList(raw, separator);
    setDraft(raw);
    setEmitted(next.join(join));
    onChange(next);
  };

  return { text: draft, setText };
}

/**
 * Spezza il testo grezzo nella lista da salvare: voci ripulite ai bordi, righe
 * vuote fuori. Va usata al salvataggio o in uscita da un campo, **non** per
 * ricalcolare ciò che il campo mostra mentre lo si sta scrivendo.
 */
export function splitList(raw: string, separator: string): string[] {
  return raw
    .split(separator)
    .map((s) => s.trim())
    .filter(Boolean);
}
