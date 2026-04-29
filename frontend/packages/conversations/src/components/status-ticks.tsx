'use client';

import { cn } from '@reloop/ui';
import { AlertTriangle, Check, CheckCheck, Clock } from 'lucide-react';
import type { MessageStatus } from '../types';

interface StatusTicksProps {
  status: MessageStatus;
  className?: string;
}

export function StatusTicks({ status, className }: StatusTicksProps) {
  if (status === 'pending') {
    return <Clock className={cn('h-3 w-3 opacity-70', className)} aria-label="In attesa" />;
  }
  if (status === 'failed') {
    return (
      <AlertTriangle
        className={cn('h-3 w-3 text-destructive', className)}
        aria-label="Errore di invio"
      />
    );
  }
  if (status === 'sent') {
    return <Check className={cn('h-3 w-3 opacity-90', className)} aria-label="Inviato" />;
  }
  if (status === 'delivered') {
    return (
      <CheckCheck className={cn('h-3 w-3 opacity-90', className)} aria-label="Consegnato" />
    );
  }
  if (status === 'read') {
    return (
      <CheckCheck className={cn('h-3 w-3 text-primary', className)} aria-label="Letto" />
    );
  }
  return null;
}
