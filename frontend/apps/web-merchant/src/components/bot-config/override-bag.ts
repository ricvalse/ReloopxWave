/** Conversione fra bag di override annidato e form piatto a chiavi dotted.
 *
 * Il backend legge gli override con `_lookup`, che cammina la forma **annidata**
 * (`{conversation: {playbook: {goal: …}}}`) e ricade su quella piatta solo per
 * retrocompatibilità. Scriviamo quindi sempre annidato.
 *
 * Condiviso fra l'editor della configurazione del merchant e quello del
 * comportamento per-profilo: i profili sono un delta della **stessa** shape
 * (`BotConfigSchema`), quindi due implementazioni divergenti di queste due
 * funzioni produrrebbero bag che il resolver legge in modo diverso.
 */

export type OverrideBag = Record<string, Record<string, unknown>>;
/** Stato del form: chiavi dotted, valori foglia. */
export type FormState = Record<string, unknown>;

export function flatten(
  obj: Record<string, unknown>,
  prefix = '',
  out: FormState = {},
): FormState {
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      flatten(value as Record<string, unknown>, path, out);
    } else {
      out[path] = value;
    }
  }
  return out;
}

export function inflate(flat: FormState): OverrideBag {
  const out: OverrideBag = {};
  for (const [path, value] of Object.entries(flat)) {
    if (value === null || value === undefined) continue;
    const parts = path.split('.');
    if (parts.length === 0) continue;
    const leaf = parts[parts.length - 1] as string;
    let node: Record<string, unknown> = out as unknown as Record<string, unknown>;
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i] as string;
      if (!Object.prototype.hasOwnProperty.call(node, seg) || typeof node[seg] !== 'object') {
        node[seg] = {};
      }
      node = node[seg] as Record<string, unknown>;
    }
    node[leaf] = value;
  }
  return out;
}
