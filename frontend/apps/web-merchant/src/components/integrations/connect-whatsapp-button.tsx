'use client';

import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

const POPUP_W = 600;
const POPUP_H = 900;
const POPUP_TIMEOUT_MS = 10 * 60 * 1000; // 10 min — match amalia-ai

type Props = {
  merchantId: string;
  /** Re-rendered after Embedded Signup completes; lets the parent flip status. */
  onPopupClosed?: () => void;
  /** Override the default Italian label (e.g. for "Riconnetti"). */
  label?: string;
  pending?: boolean;
};

/**
 * Opens 360dialog's hosted Embedded Signup in a popup. After the merchant
 * completes Meta Business signup, 360dialog redirects to a URL pre-configured
 * in the Partner Hub admin (typically `/integrations` on the merchant portal),
 * where a useEffect picks up the `?client=&channels=` params and POSTs them to
 * `/integrations/whatsapp/channels`. This component only opens the popup — it
 * doesn't handle the callback itself.
 */
export function ConnectWhatsAppButton({ merchantId, onPopupClosed, label, pending }: Props) {
  const [opening, setOpening] = useState(false);
  const closeWatcherRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const partnerId = useQuery({
    queryKey: ['integrations', 'whatsapp', 'partner-id'],
    queryFn: async (): Promise<string> => {
      const api = getApiClient();
      const { data, error } = await api.GET(
        '/integrations/whatsapp/partner-id' as never,
        {} as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return (data as { partner_id: string }).partner_id;
    },
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    return () => {
      if (closeWatcherRef.current) clearInterval(closeWatcherRef.current);
    };
  }, []);

  const handleClick = () => {
    if (!partnerId.data) return;
    setOpening(true);

    const url =
      `https://hub.360dialog.com/dashboard/app/${partnerId.data}` +
      `/permissions?store_id=${encodeURIComponent(merchantId)}`;

    const left = Math.max(0, (window.screen.width - POPUP_W) / 2);
    const top = Math.max(0, (window.screen.height - POPUP_H) / 2);
    const popup = window.open(
      url,
      'd360-embedded-signup',
      `width=${POPUP_W},height=${POPUP_H},left=${left},top=${top},menubar=no,toolbar=no,location=no`,
    );

    if (!popup) {
      setOpening(false);
      // Browser blocked the popup. Fall back to a full-page redirect — the
      // user will return to the same `/integrations` URL afterwards.
      window.location.href = url;
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

  const disabled = pending || opening || partnerId.isLoading || !partnerId.data;

  return (
    <div className="flex flex-col items-end gap-1">
      <Button onClick={handleClick} disabled={disabled}>
        {opening ? 'Apertura…' : (label ?? 'Collega WhatsApp')}
      </Button>
      {partnerId.isError ? (
        <p className="text-xs text-destructive">
          Impossibile leggere il Partner ID. Contatta l&apos;amministratore.
        </p>
      ) : null}
    </div>
  );
}
