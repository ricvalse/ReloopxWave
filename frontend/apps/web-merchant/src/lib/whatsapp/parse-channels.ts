/**
 * Extract the channel ID from 360dialog's redirect URL.
 *
 * After Embedded Signup, 360dialog redirects with `?channels=[...]` where
 * the value is sometimes valid JSON (`["abc"]`) and sometimes the quirky
 * unquoted format (`[abc]`). Both must round-trip to the same `channel_id`
 * before we POST to `/integrations/whatsapp/channels`.
 *
 * Ported from amalia-ai/apps/web/lib/whatsapp/utils.ts:14-52.
 */
export function parseChannels(raw: string | null): string[] {
  if (!raw) return [];
  const trimmed = raw.trim();
  if (!trimmed) return [];

  // Strip optional surrounding brackets so we can handle both forms.
  const stripped =
    trimmed.startsWith('[') && trimmed.endsWith(']')
      ? trimmed.slice(1, -1)
      : trimmed;
  if (!stripped) return [];

  // Try strict JSON first — covers `["abc","def"]` and `"abc"`.
  try {
    const parsed = JSON.parse(`[${stripped}]`) as unknown;
    if (Array.isArray(parsed)) {
      return parsed.filter((v): v is string => typeof v === 'string' && v.length > 0);
    }
  } catch {
    // Fall through to comma-split.
  }

  // Fallback: bare CSV like `[abc,def]` or just `abc`.
  return stripped
    .split(',')
    .map((s) => s.trim().replace(/^"|"$/g, ''))
    .filter((s) => s.length > 0);
}
