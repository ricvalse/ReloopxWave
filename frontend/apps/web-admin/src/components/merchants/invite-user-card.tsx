'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

type UserOut = components['schemas']['UserOut'];

export function InviteUserCard({ merchantId }: { merchantId: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const users = useQuery({
    queryKey: ['users', 'list', merchantId],
    queryFn: async (): Promise<UserOut[]> => {
      const api = getApiClient();
      const { data, error: e } = await api.GET('/users/' as never, {
        params: { query: { merchant_id: merchantId } },
      } as never);
      if (e) throw new Error(typeof e === 'string' ? e : JSON.stringify(e));
      return data as UserOut[];
    },
  });

  const invite = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const body: Record<string, unknown> = {
        email,
        role: 'merchant_user',
        merchant_id: merchantId,
      };
      if (fullName.trim()) body.full_name = fullName.trim();
      const { data, error: e } = await api.POST('/users/invite' as never, {
        body,
      } as never);
      if (e) {
        const msg =
          typeof e === 'string'
            ? e
            : (e as { error?: { message?: string } })?.error?.message ?? JSON.stringify(e);
        throw new Error(msg);
      }
      return data as UserOut;
    },
    onSuccess: (u) => {
      setFlash(`Invito inviato a ${u.email}.`);
      setEmail('');
      setFullName('');
      setError(null);
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ['users', 'list', merchantId] });
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : 'Errore invio invito');
      setFlash(null);
    },
  });

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Utenti merchant</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            Inviti che permettono ai membri del merchant di accedere al portale.
          </p>
        </div>
        <Button size="sm" onClick={() => setOpen((v) => !v)} disabled={invite.isPending}>
          {open ? 'Annulla' : '+ Invita utente'}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {open ? (
          <form
            className="space-y-3 border-b pb-4"
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              invite.mutate();
            }}
          >
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="invite-email">
                  Email
                </label>
                <input
                  id="invite-email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="invite-name">
                  Nome (opzionale)
                </label>
                <input
                  id="invite-name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              L&apos;utente riceve un magic link da Supabase: clicca, atterra
              su web-merchant e accede a questo merchant come{' '}
              <code className="rounded bg-muted px-1">merchant_user</code>.
            </p>
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            <div className="flex justify-end">
              <Button type="submit" disabled={invite.isPending || !email}>
                {invite.isPending ? 'Invio…' : 'Invia invito'}
              </Button>
            </div>
          </form>
        ) : null}

        {flash ? (
          <p className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
            {flash}
          </p>
        ) : null}

        {users.isLoading ? (
          <p className="text-sm text-muted-foreground">Caricamento utenti…</p>
        ) : users.isError ? (
          <p className="text-sm text-destructive">
            {users.error instanceof Error ? users.error.message : 'Errore'}
          </p>
        ) : (users.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessun utente invitato.</p>
        ) : (
          <ul className="divide-y">
            {(users.data ?? []).map((u) => (
              <li key={u.id} className="flex items-center justify-between py-2 text-sm">
                <div>
                  <p className="font-medium">{u.full_name || u.email}</p>
                  {u.full_name ? (
                    <p className="text-xs text-muted-foreground">{u.email}</p>
                  ) : null}
                </div>
                <span className="rounded bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                  {u.role}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
