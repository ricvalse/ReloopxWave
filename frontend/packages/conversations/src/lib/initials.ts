/**
 * Avatar initials for the thread list. Falls back to the last 4 digits of the
 * phone number if no contact name is available — matches the WhatsApp pattern.
 */
export function contactInitials(name: string | null | undefined, phone: string | null): string {
  if (name) {
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
    return (parts[0]!.charAt(0) + parts[parts.length - 1]!.charAt(0)).toUpperCase();
  }
  if (phone) {
    const digits = phone.replace(/\D/g, '');
    return digits.slice(-4) || '??';
  }
  return '??';
}

export function contactDisplayName(
  name: string | null | undefined,
  phone: string | null,
): string {
  return name ?? phone ?? '—';
}
