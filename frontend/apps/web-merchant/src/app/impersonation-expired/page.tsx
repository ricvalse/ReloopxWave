/**
 * Landing shown when an impersonation session ends (token expired / absent /
 * overwritten by another tab). An impersonating admin has no merchant
 * credentials, so we never send them to `/login` — they re-enter from the
 * agency panel instead.
 *
 * `imp-access-token` is one cookie shared by every tab on this origin, not
 * scoped per merchant. Opening "Entra come merchant" for a different merchant
 * (or clicking "Esci") in another tab overwrites/clears it here too — that's
 * the `reason=switched` case, distinct from a plain 20-minute timeout.
 */
export default async function ImpersonationExpiredPage({
  searchParams,
}: {
  searchParams: Promise<{ reason?: string }>;
}) {
  const switched = (await searchParams).reason === 'switched';
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="max-w-md text-center">
        <h1 className="text-lg font-semibold">Sessione di impersonazione terminata</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {switched
            ? 'Hai aperto un’altra sessione di impersonazione (probabilmente in un’altra scheda del browser) e questa non è più valida.'
            : 'La sessione con cui stavi configurando il merchant è scaduta.'}{' '}
          Per continuare, torna al pannello agenzia e clicca di nuovo
          “Entra come merchant”.
        </p>
      </div>
    </div>
  );
}
