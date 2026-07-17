'use client';

import { cn } from '@reloop/ui';
import { AlertCircle, Download, FileText, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useMessageMedia } from '../hooks/use-message-media';
import type { Message } from '../types';

interface MessageMediaProps {
  message: Message;
  /** Meta color from the parent bubble, so captions/labels stay readable. */
  metaColor: string;
}

const ERROR_LABEL: Record<string, string> = {
  too_large: 'File troppo grande',
  unsupported_mime: 'Formato non supportato',
  download_failed: 'Download non riuscito',
  storage_failed: 'Salvataggio non riuscito',
};

function Unavailable({ label, metaColor }: { label: string; metaColor: string }) {
  return (
    <div className={cn('flex items-center gap-1.5 py-1 text-xs', metaColor)}>
      <AlertCircle className="h-3.5 w-3.5" />
      <span>{label}</span>
    </div>
  );
}

/**
 * Renders the media attachment for a message (image/audio/video/document).
 * Loads a short-lived signed URL on demand; shows a placeholder while the
 * download is pending (two-phase write) or on failure. Images open in a
 * hand-rolled lightbox (Escape/backdrop to close).
 */
export function MessageMedia({ message, metaColor }: MessageMediaProps) {
  const media = message.meta?.media;
  const [lightbox, setLightbox] = useState(false);

  const hasStored = !!media?.storage_path;
  const { data: url, isLoading, isError } = useMessageMedia(
    message.conversation_id,
    message.id,
    hasStored,
  );

  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setLightbox(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [lightbox]);

  if (!media) return null;

  const kind = media.kind ?? 'document';

  // Download/store failed → legible reason, no broken bubble.
  if (media.error) {
    return <Unavailable label={ERROR_LABEL[media.error] ?? 'Contenuto non disponibile'} metaColor={metaColor} />;
  }
  // Pending (storage_path not yet written) or the signed URL is loading/errored.
  if (!hasStored || isLoading) {
    return (
      <div className={cn('flex items-center gap-1.5 py-1 text-xs', metaColor)}>
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        <span>Caricamento…</span>
      </div>
    );
  }
  if (isError || !url) {
    return <Unavailable label="Contenuto non disponibile" metaColor={metaColor} />;
  }

  if (kind === 'image' || kind === 'sticker') {
    return (
      <>
        <button
          type="button"
          onClick={() => setLightbox(true)}
          className="mb-1 block overflow-hidden rounded-lg focus:outline-none focus:ring-2 focus:ring-white/60"
        >
          {/* Signed Supabase URL — the Next image optimizer can't cache it, and
              the host isn't in remotePatterns, so use a plain lazy <img>. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={media.caption ?? 'Immagine'}
            loading="lazy"
            className="max-h-64 max-w-full rounded-lg object-cover"
          />
        </button>
        {lightbox && (
          <div
            className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-4"
            onClick={() => setLightbox(false)}
            role="dialog"
            aria-modal="true"
            aria-label="Anteprima immagine"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={url}
              alt={media.caption ?? 'Immagine'}
              className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain"
            />
          </div>
        )}
      </>
    );
  }

  if (kind === 'audio') {
    return (
      <div className="mb-1 flex flex-col gap-1">
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <audio controls src={url} className="max-w-full" />
        {media.transcription && (
          <span className={cn('text-xs italic', metaColor)}>“{media.transcription}”</span>
        )}
      </div>
    );
  }

  if (kind === 'video') {
    return (
      <div className="mb-1">
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <video controls src={url} className="max-h-64 max-w-full rounded-lg" />
      </div>
    );
  }

  // document (and any other stored kind): a download affordance.
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      download
      className={cn(
        'mb-1 flex items-center gap-2 rounded-lg border border-black/10 bg-black/5 px-2.5 py-2 text-sm hover:bg-black/10 dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10',
      )}
    >
      <FileText className="h-4 w-4 shrink-0 opacity-70" />
      <span className="min-w-0 flex-1 truncate">{media.caption ?? 'Documento'}</span>
      <Download className="h-4 w-4 shrink-0 opacity-70" />
    </a>
  );
}
