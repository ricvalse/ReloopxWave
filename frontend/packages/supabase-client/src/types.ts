/**
 * Supabase DB types — placeholder until generated from the live schema.
 *
 * To regenerate:
 *   npx supabase gen types typescript --project-id <ref> > src/generated-types.ts
 * then re-export from here.
 */
export type Database = {
  public: {
    Tables: Record<string, { Row: Record<string, unknown> }>;
    Views: Record<string, never>;
    Functions: Record<string, never>;
    Enums: Record<string, never>;
  };
};
