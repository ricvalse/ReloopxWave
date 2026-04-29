'use client';

import { Button } from '@reloop/ui';
import { AlertTriangle } from 'lucide-react';
import { useEffect } from 'react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-6">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="h-5 w-5 text-destructive" />
      </div>
      <div className="text-center">
        <p className="text-sm font-medium">Si è verificato un errore</p>
        <p className="mt-1 text-xs text-muted-foreground">{error.message}</p>
      </div>
      <Button variant="outline" size="sm" onClick={reset}>
        Riprova
      </Button>
    </div>
  );
}
