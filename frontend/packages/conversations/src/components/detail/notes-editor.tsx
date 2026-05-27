'use client';

import { Textarea, cn } from '@reloop/ui';
import { AlertCircle, Check, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useUpdateNotes } from '../../hooks/use-update-notes';

const DEBOUNCE_MS = 1750;
const SAVED_FLASH_MS = 2000;

type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error';

interface NotesEditorProps {
  conversationId: string;
  /** Server value from the conversations list cache. */
  note: string | null | undefined;
}

export function NotesEditor({ conversationId, note }: NotesEditorProps) {
  const updateNotes = useUpdateNotes();
  const [value, setValue] = useState(note ?? '');
  const [state, setState] = useState<SaveState>('idle');

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flashRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSaved = useRef(note ?? '');
  const stateRef = useRef(state);
  stateRef.current = state;

  // Hard reset when switching threads.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    setValue(note ?? '');
    lastSaved.current = note ?? '';
    setState('idle');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // Adopt the server note when it arrives asynchronously (the note loads after
  // mount) or changes underneath us — but only while idle, so we never clobber
  // what the agent is mid-typing.
  useEffect(() => {
    if (stateRef.current === 'idle' || stateRef.current === 'saved') {
      setValue(note ?? '');
      lastSaved.current = note ?? '';
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note]);

  const flush = useCallback(
    (next: string) => {
      const normalized = next.trim();
      if (normalized === lastSaved.current.trim()) {
        setState('idle');
        return;
      }
      setState('saving');
      updateNotes.mutate(
        { conversationId, note: normalized || null },
        {
          onSuccess: () => {
            lastSaved.current = normalized;
            setState('saved');
            if (flashRef.current) clearTimeout(flashRef.current);
            flashRef.current = setTimeout(() => setState('idle'), SAVED_FLASH_MS);
          },
          onError: () => setState('error'),
        },
      );
    },
    [conversationId, updateNotes],
  );

  const onChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    setValue(next);
    setState('dirty');
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => flush(next), DEBOUNCE_MS);
  };

  const onBlur = () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    flush(value);
  };

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (flashRef.current) clearTimeout(flashRef.current);
    },
    [],
  );

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Note interne
        </p>
        <SaveIndicator state={state} />
      </div>
      <Textarea
        value={value}
        onChange={onChange}
        onBlur={onBlur}
        placeholder="Aggiungi una nota privata su questa conversazione…"
        className="min-h-[88px] resize-none text-[13px] leading-snug"
        aria-label="Note interne sulla conversazione"
      />
    </div>
  );
}

function SaveIndicator({ state }: { state: SaveState }) {
  if (state === 'saving') {
    return (
      <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Salvataggio…
      </span>
    );
  }
  if (state === 'saved') {
    return (
      <span className="flex items-center gap-1 text-[11px] text-success">
        <Check className="h-3 w-3" />
        Salvato
      </span>
    );
  }
  if (state === 'error') {
    return (
      <span className="flex items-center gap-1 text-[11px] text-destructive">
        <AlertCircle className="h-3 w-3" />
        Errore — riprova
      </span>
    );
  }
  if (state === 'dirty') {
    return <span className={cn('text-[11px] text-muted-foreground/60')}>Modifiche in corso…</span>;
  }
  return null;
}
