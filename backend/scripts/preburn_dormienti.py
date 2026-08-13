"""Timbra come «già gestiti» gli episodi di dormienza arretrati, prima del deploy.

Run da backend/ (prova a secco: non scrive nulla):

    export SUPABASE_DB_URL='postgresql://postgres.<ref>:<pwd>@aws-0-eu-west-1.pooler.supabase.com:6543/postgres'
    uv run python scripts/preburn_dormienti.py
    uv run python scripts/preburn_dormienti.py --applica

Usare la porta **6543** (transaction mode): la 5432 è session mode con 15 slot e
si satura. Senza `SUPABASE_DB_URL` in ambiente lo script prova `.env.local`.

**A cosa serve.** L'emettitore UC-06 è edge-triggered: emette una volta per
episodio di dormienza e se ne ricorda in `leads.meta.dormant_fired_for`. Fino al
2026-08-11 quell'ancora non è mai stata scritta (l'UPDATE che la scrive era SQL
non valido — vedi `tests/unit/test_sql_bind_params.py`), quindi ogni episodio
accumulato risulta «mai notificato»: al primo giro dopo il deploy partirebbero
tutti insieme. Su Recruiting DM erano 218 messaggi in una volta.

**Cosa timbra, e cosa no.**
  * SOLO i lead **già oltre la soglia** del proprio merchant. Il loro episodio
    corrente viene chiuso e non emetterà; si riarma da solo se il lead risponde
    (un nuovo messaggio sposta `last_interaction_at` oltre l'ancora).
  * NON i lead ancora sotto soglia. Timbrarli li zittirebbe per sempre proprio
    quando maturano, cioè il caso che il merchant vuole vedere funzionare.

La soglia è quella configurata sul nodo trigger di ciascun merchant (`min` fra le
sue automazioni dormienti attive, come fa `_threshold_days`), non una costante:
così vale anche per merchant diversi da quello per cui è nato.

Idempotente: rilanciarlo timbra solo ciò che nel frattempo ha superato la soglia.
**Va rilanciato subito prima del deploy** se fra la prima passata e il push passa
del tempo — l'arretrato si riforma al ritmo con cui i lead superano la soglia.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg

_ENV_LOCAL = Path(__file__).resolve().parent.parent / ".env.local"

# I candidati: chi ha superato la soglia del proprio merchant. Ricalcolata sia
# per il conteggio sia per la verifica finale, così le due non possono divergere.
CANDIDATI = """
with soglie as (
  select f.merchant_id,
         min(coalesce(nullif(n.config->>'days','')::int, 90)) giorni
  from automation_flows f
  join automation_nodes n on n.automation_id = f.id and n.kind = 'trigger'
  where f.trigger_type = 'lead_dormant' and f.enabled
  group by 1),
li as (
  select l.id, l.merchant_id, max(c.last_message_at) ultima
  from leads l
  join conversations c on c.lead_id = l.id
  where c.last_message_at is not null
  group by 1, 2)
select li.id, li.ultima, m.name merchant, s.giorni
from li
join soglie s on s.merchant_id = li.merchant_id
join merchants m on m.id = li.merchant_id
where li.ultima < now() - make_interval(days => s.giorni)
"""

TIMBRA = """
with soglie as (
  select f.merchant_id,
         min(coalesce(nullif(n.config->>'days','')::int, 90)) giorni
  from automation_flows f
  join automation_nodes n on n.automation_id = f.id and n.kind = 'trigger'
  where f.trigger_type = 'lead_dormant' and f.enabled
  group by 1),
li as (
  select l.id, l.merchant_id, max(c.last_message_at) ultima
  from leads l
  join conversations c on c.lead_id = l.id
  where c.last_message_at is not null
  group by 1, 2)
update leads l
set meta = jsonb_set(coalesce(l.meta, '{}'::jsonb),
                     '{dormant_fired_for}',
                     to_jsonb(li.ultima::text))
from li
join soglie s on s.merchant_id = li.merchant_id
where li.id = l.id
  and li.ultima < now() - make_interval(days => s.giorni)
"""


def dsn() -> str:
    raw = os.environ.get("SUPABASE_DB_URL")
    if not raw and _ENV_LOCAL.exists():
        found = re.search(r"SUPABASE_DB_URL=(\S+)", _ENV_LOCAL.read_text())
        raw = found.group(1) if found else None
    if not raw:
        raise SystemExit("SUPABASE_DB_URL non impostata (né in ambiente né in .env.local)")
    return raw.replace("postgresql+asyncpg://", "postgresql://").replace(":5432/", ":6543/")


async def main(applica: bool) -> None:
    conn = await asyncpg.connect(dsn(), statement_cache_size=0)
    try:
        righe = await conn.fetch(CANDIDATI)
        per_merchant: dict[tuple[str, int], int] = {}
        for r in righe:
            chiave = (r["merchant"], r["giorni"])
            per_merchant[chiave] = per_merchant.get(chiave, 0) + 1

        print("episodi arretrati da timbrare (oltre la soglia del merchant):")
        for (merchant, giorni), n in sorted(per_merchant.items()):
            print(f"  {merchant:<20} soglia {giorni:>3} giorni  ->  {n:>4} lead")
        print(f"  TOTALE: {len(righe)}")

        prima = await conn.fetchval("select count(*) from leads where meta ? 'dormant_fired_for'")
        print(f"\nancore già presenti: {prima}")

        if not applica:
            print("\n(prova a secco: nessuna scrittura. Rilanciare con --applica)")
            return

        async with conn.transaction():
            esito = await conn.execute(TIMBRA)
        print(f"\nscritto: {esito}")

        dopo = await conn.fetchval("select count(*) from leads where meta ? 'dormant_fired_for'")
        print(f"ancore presenti dopo la scrittura: {dopo}")

        # Nessun candidato oltre soglia deve essere rimasto senza un'ancora almeno
        # pari alla propria ultima interazione: se ne resta uno, al primo giro emette.
        # Riusa `CANDIDATI` invece di riscrivere la CTE, così il controllo non può
        # divergere da ciò che è stato timbrato. Nell'interpolazione non entra
        # niente che venga da fuori: è una costante di modulo.
        verifica = (
            "with cand as ("  # noqa: S608
            + CANDIDATI
            + ") select count(*) from cand join leads l on l.id = cand.id"
            " where (l.meta->>'dormant_fired_for') is null"
            "    or (l.meta->>'dormant_fired_for')::timestamptz < cand.ultima"
        )
        residui = await conn.fetchval(verifica)
        print(f"controllo — candidati oltre soglia rimasti senza ancora: {residui}")
        if residui:
            raise SystemExit(f"ATTENZIONE: {residui} candidati emetterebbero comunque")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(applica="--applica" in sys.argv))
