'use client';

import { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Button } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

const POPUP_W = 600;
const POPUP_H = 900;
const POPUP_TIMEOUT_MS = 10 * 60 * 1000;

type Props = {
  /** Re-rendered after Embedded Signup completes; lets the parent flip status. */
  onPopupClosed?: () => void;
  /** Override the default Italian label (e.g. for "Riconnetti"). */
  label?: string;
  pending?: boolean;
};

/**
 * Opens 360dialog's hosted Embedded Signup via the Wave Marketing router.
 *
 * Flow (see NEWPLATFORM_SETUP.md § Phase B4):
 *   1. POST `/integrations/whatsapp/onboard/start` on our backend — that
 *      call mints a one-shot state token on the router and returns the
 *      assembled hub URL we should navigate the merchant to.
 *   2. Open that URL in a popup. The Partner ID and router callback URL
 *      are already baked into `signup_url` by the backend.
 *   3. After signup, 360dialog redirects to the router's `/onboard/callback`,
 *      which fetches the per-channel key, fires
 *      `POST /internal/whatsapp-connected` to us, and finally bounces the
 *      browser to our `return_url` (the parent panel's `/integrations`
 *      page with `?provider=whatsapp&status=connected`). The popup-close
 *      watcher below re-fetches `/integrations/status` to flip the UI.
 */
export function ConnectWhatsAppButton({ onPopupClosed, label, pending }: Props) {
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

  useEffect(() => {
    return () => {
      if (closeWatcherRef.current) clearInterval(closeWatcherRef.current);
    };
  }, []);

  const handleClick = async () => {
    setOpening(true);
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

  const disabled = pending || opening || startOnboard.isPending;

  return (
    <div className="flex flex-col items-end gap-1">
      <Button onClick={handleClick} disabled={disabled}>
        {opening || startOnboard.isPending ? 'Apertura…' : (label ?? 'Collega WhatsApp')}
      </Button>
      {startOnboard.isError ? (
        <p className="text-xs text-destructive">
          Impossibile avviare la procedura. Riprova tra qualche secondo.
        </p>
      ) : null}
    </div>
  );
}
