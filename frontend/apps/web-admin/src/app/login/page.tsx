'use client';

import { useState } from 'react';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { useRouter } from 'next/navigation';
import { getBrowserSupabase } from '@/lib/supabase';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const supabase = getBrowserSupabase();
      const { data, error: authError } = await supabase.auth.signInWithPassword({ email, password });
      if (authError) throw authError;

      // Freshly signed-in sessions may not yet carry tenant_id/role claims.
      // `POST /auth/bootstrap` handles three cases:
      //  - first ever admin  → creates the Wave tenant, promotes caller
      //  - returning admin   → 200, no-op
      //  - anyone else       → 409, we sign out and surface the message
      const token = data.session?.access_token;
      if (!token) throw new Error('Login riuscito ma sessione non disponibile.');

      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
      if (!apiBase) throw new Error('NEXT_PUBLIC_API_BASE_URL not configured');
      const resp = await fetch(`${apiBase.replace(/\/$/, '')}/auth/bootstrap`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) {
        const body = (await resp.json().catch(() => ({}))) as {
          error?: { message?: string; code?: string };
        };
        const code = body.error?.code ?? '';
        if (resp.status === 409 && code === 'already_bootstrapped') {
          await supabase.auth.signOut();
          throw new Error(
            "L'admin di Wave Marketing è già stato configurato. Chiedi un invito per accedere.",
          );
        }
        throw new Error(body.error?.message ?? `Bootstrap failed (${resp.status})`);
      }
      const payload = (await resp.json()) as { requires_reauth?: boolean; created?: boolean };

      // When the backend has just promoted this user, the JWT in memory still
      // lacks the new claims — refresh it so the next API call sees tenant_id.
      if (payload.requires_reauth || payload.created) {
        await supabase.auth.refreshSession();
      }

      router.push('/dashboard');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Admin</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="email">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            <Button type="submit" className="w-full" disabled={pending}>
              {pending ? 'Accesso…' : 'Accedi'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
