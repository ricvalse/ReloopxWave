'use client';

import { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Button } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

const POPUP_W = 600;
const POPUP_H = 900;
const POPUP_TIMEOUT_MS = 10 * 60 * 1000;

type Props = {
  /** Called after the signup popup closes; lets the parent refetch status. */
  onPopupClosed?: () => void;
  /** Override the default Italian label. */
  label?: string;
  pending?: boolean;
  /**
   * When true, click first hard-disconnects the existing channel (`POST
   * /integrations/whatsapp/disconnect`) and then opens Embedded Signup as if
   * starting fresh. Used by the "Sostituisci canale" affordance.
   */
  reconnect?: boolean;
  /** Called right after disconnect succeeds — parent should refetch status so
   *  the UI reflects "no channel" before the popup opens. */
  onDisconnected?: () => void;
};

/**
 * Opens 360dialog's Embedded Signup via the Relooptech router.
 *
 * Flow:
 *   1. (Reconnect path only) POST `/integrations/whatsapp/disconnect` to wipe
 *      the existing integration row.
 *   2. POST `/integrations/whatsapp/onboard/start` — the backend asks the
 *      router for a one-shot state token; the router returns the assembled
 *      `connect_url` (partner_id lives only on the router).
 *   3. Open `connect_url` in a popup. After signup, 360dialog redirects to
 *      the router's `/onboard/callback`, the router fetches the per-channel
 *      D360 key, fires `POST /internal/whatsapp-connected` to us, and
 *      finally bounces the browser to `return_url`. The popup-close watcher
 *      below re-fetches `/integrations/status` to flip the UI.
 */
export function ConnectWhatsAppButton({
  onPopupClosed,
  label,
  pending,
  reconnect,
  onDisconnected,
}: Props) {
  const [opening, setOpening] = useState(false);
  const closeWatcherRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startOnboard = useMutation({
    mutationFn: async (): Promise<{ signup_url: string }> => {
      const api = getApiClient();
      const { data, error } = await api.POST(
        '/integrations/whatsapp/onboard/start' as never,
        { body: {} } as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as { signup_url: string };
    },
  });

  const disconnect = useMutation({
    mutationFn: async (): Promise<void> => {
      const api = getApiClient();
      const { error } = await api.POST(
        '/integrations/whatsapp/disconnect' as never,
        {} as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
    },
  });

  useEffect(() => {
    return () => {
      if (closeWatcherRef.current) clearInterval(closeWatcherRef.current);
    };
  }, []);

  const handleClick = async () => {
    if (reconnect) {
      const confirmed = window.confirm(
        'Sostituire il canale WhatsApp? Il numero attuale verrà scollegato e dovrai completare di nuovo la procedura di 360dialog.',
      );
      if (!confirmed) return;
    }

    setOpening(true);

    if (reconnect) {
      try {
        await disconnect.mutateAsync();
      } catch {
        setOpening(false);
        return;
      }
      // Refetch status so the panel shows "Nessun numero collegato" while
      // the popup is open.
      onDisconnected?.();
    }

    let signupUrl: string;
    try {
      const { signup_url } = await startOnboard.mutateAsync();
      signupUrl = signup_url;
    } catch {
      setOpening(false);
      return;
    }

    const left = Math.max(0, (window.screen.width - POPUP_W) / 2);
    const top = Math.max(0, (window.screen.height - POPUP_H) / 2);
    const popup = window.open(
      signupUrl,
      'd360-embedded-signup',
      `width=${POPUP_W},height=${POPUP_H},left=${left},top=${top},menubar=no,toolbar=no,location=no`,
    );

    if (!popup) {
      // Browser blocked the popup. Fall back to a full-page redirect.
      window.location.href = signupUrl;
      return;
    }

    const startedAt = Date.now();
    closeWatcherRef.current = setInterval(() => {
      const expired = Date.now() - startedAt > POPUP_TIMEOUT_MS;
      if (popup.closed || expired) {
        if (closeWatcherRef.current) clearInterval(closeWatcherRef.current);
        closeWatcherRef.current = null;
        setOpening(false);
        if (!expired) onPopupClosed?.();
      }
    }, 1000);
  };

  const disabled =
    pending || opening || startOnboard.isPending || disconnect.isPending;

  const buttonText = (() => {
    if (disconnect.isPending) return 'Scollegamento…';
    if (opening || startOnboard.isPending) return 'Apertura…';
    return label ?? 'Collega WhatsApp';
  })();

  return (
    <div className="flex flex-col items-end gap-1">
      <Button onClick={handleClick} disabled={disabled}>
        {buttonText}
      </Button>
      {disconnect.isError ? (
        <p className="text-xs text-destructive">
          Scollegamento fallito. Riprova tra qualche secondo.
        </p>
      ) : startOnboard.isError ? (
        <p className="text-xs text-destructive">
          Impossibile avviare la procedura. Riprova tra qualche secondo.
        </p>
      ) : null}
    </div>
  );
}
