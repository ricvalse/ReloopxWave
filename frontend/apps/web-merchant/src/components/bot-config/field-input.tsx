'use client';

import { useQuery } from '@tanstack/react-query';
import { Input, Select, Switch, Textarea } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';
import type { FieldDef } from './sections';

/**
 * The controls for one config field.
 *
 * Every control here comes from the design system. The hand-rolled versions
 * this replaces used `bg-background`, which in dark mode is *darker* than the
 * card they sit in — inputs read as holes punched in the surface. The design
 * system's `bg-transparent` sits flush with the card, which is what the rest of
 * the product looks like.
 */
export function FieldInput({
  field,
  value,
  disabled,
  onChange,
  placeholder,
}: {
  field: FieldDef;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
  placeholder?: string;
}) {
  if (field.kind === 'bool') {
    return (
      <Switch
        id={field.key}
        disabled={disabled}
        checked={!!value}
        onCheckedChange={(v) => onChange(v)}
      />
    );
  }

  if (field.kind === 'int' || field.kind === 'float') {
    return (
      <Input
        id={field.key}
        type="number"
        disabled={disabled}
        min={field.min}
        max={field.max}
        step={field.step ?? (field.kind === 'int' ? 1 : 0.01)}
        value={value === null || value === undefined ? '' : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === '') {
            onChange(null);
            return;
          }
          const n = field.kind === 'int' ? parseInt(raw, 10) : parseFloat(raw);
          onChange(Number.isNaN(n) ? null : n);
        }}
        className="w-28 tabular-nums"
      />
    );
  }

  if (field.kind === 'textarea') {
    return (
      <Textarea
        id={field.key}
        disabled={disabled}
        value={value === null || value === undefined ? '' : String(value)}
        onChange={(e) => onChange(e.target.value || null)}
        placeholder={placeholder ?? field.placeholder}
        rows={field.rows ?? 3}
        className="w-full"
      />
    );
  }

  if (field.kind === 'select') {
    return (
      <Select
        id={field.key}
        disabled={disabled}
        value={value === null || value === undefined ? '' : String(value)}
        onChange={(e) => onChange(e.target.value || null)}
      >
        {field.options?.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    );
  }

  if (field.kind === 'calendar') {
    return <CalendarFieldInput value={value} disabled={disabled} onChange={onChange} />;
  }

  if (field.kind === 'tags') {
    // Value is a string[]; render one item per line. Emit null when empty so
    // `inflate` drops it and the field reads as inherited (not an empty override).
    const arr = Array.isArray(value) ? (value as unknown[]).map(String) : [];
    return (
      <Textarea
        id={field.key}
        disabled={disabled}
        value={arr.join('\n')}
        onChange={(e) => {
          const lines = e.target.value
            .split('\n')
            .map((s) => s.trim())
            .filter(Boolean);
          onChange(lines.length ? lines : null);
        }}
        placeholder={placeholder ?? field.placeholder}
        rows={field.rows ?? 3}
        className="w-full font-mono text-[13px] leading-relaxed"
      />
    );
  }

  if (field.kind === 'multiselect') {
    // Value is a string[] allowlist. Nothing selected → emit null so the field
    // reads as inherited (backend: null = all actions allowed).
    const selected = new Set(Array.isArray(value) ? (value as unknown[]).map(String) : []);
    const toggle = (opt: string, on: boolean) => {
      const next = new Set(selected);
      if (on) next.add(opt);
      else next.delete(opt);
      const arr = Array.from(next);
      onChange(arr.length ? arr : null);
    };
    return (
      <div className="grid gap-1.5 sm:grid-cols-2">
        {field.options?.map((o) => (
          <label
            key={o.value}
            className="flex cursor-pointer items-center gap-2 rounded-md border border-transparent px-2 py-1.5 text-sm transition-colors hover:border-border hover:bg-accent/40"
          >
            <input
              type="checkbox"
              disabled={disabled}
              checked={selected.has(o.value)}
              onChange={(e) => toggle(o.value, e.target.checked)}
              className="h-4 w-4 rounded border-input accent-primary"
            />
            <span>{o.label}</span>
          </label>
        ))}
      </div>
    );
  }

  return (
    <Input
      id={field.key}
      type="text"
      disabled={disabled}
      value={value === null || value === undefined ? '' : String(value)}
      onChange={(e) => onChange(e.target.value || null)}
      placeholder={placeholder ?? field.placeholder}
    />
  );
}

function CalendarFieldInput({
  value,
  disabled,
  onChange,
}: {
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  const { merchantId } = useMerchantId();
  const calendars = useQuery({
    queryKey: ['ghl', 'calendars', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<{ id: string; name: string | null }[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/integrations/ghl/calendars', {
        params: { query: { merchant_id: merchantId! } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return (data as { calendars: { id: string; name: string | null }[] }).calendars;
    },
  });

  const current = value === null || value === undefined ? '' : String(value);
  const options = calendars.data ?? [];

  // GHL not connected (or no calendars): fall back to a manual id input so the
  // booking config still works without the picker.
  if (!calendars.isLoading && !calendars.isError && options.length === 0) {
    return (
      <Input
        type="text"
        disabled={disabled}
        value={current}
        onChange={(e) => onChange(e.target.value || null)}
        placeholder="Calendar ID (GHL non collegato)"
      />
    );
  }

  const hasCurrent = options.some((c) => c.id === current);
  return (
    <Select
      disabled={disabled || calendars.isLoading}
      value={current}
      onChange={(e) => onChange(e.target.value || null)}
    >
      <option value="">{calendars.isLoading ? 'Caricamento…' : '— Seleziona calendario —'}</option>
      {options.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name || c.id}
        </option>
      ))}
      {current && !hasCurrent ? <option value={current}>{current} (corrente)</option> : null}
    </Select>
  );
}
