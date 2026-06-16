/**
 * Landing shown when an impersonation session ends (token expired / absent).
 * An impersonating admin has no merchant credentials, so we never send them to
 * `/login` — they re-enter from the agency panel instead.
 */
export default function ImpersonationExpiredPage() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="max-w-md text-center">
        <h1 className="text-lg font-semibold">Sessione di impersonazione terminata</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          La sessione con cui stavi configurando il merchant è scaduta. Per
          continuare, torna al pannello agenzia e clicca di nuovo
          “Entra come merchant”.
        </p>
      </div>
    </div>
  );
}
