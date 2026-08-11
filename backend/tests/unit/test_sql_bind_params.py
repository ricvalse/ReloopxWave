"""Guardia sull'SQL grezzo: un parametro seguito da `::` non viene legato.

Il bug, in produzione dal 2026-07-20 al 2026-08-11. `text("to_jsonb(:anchor::text)")`
sembra corretto e non lo è: SQLAlchemy riconosce i parametri con la regex

    (?<![:\\w\\$])(:(?:[\\w\\#]+|\\(.+?\\)))(?![:\\w\\$])

e il lookahead **esclude i due punti**. Con `::` subito dopo, `:anchor` non è un
parametro: resta testo letterale, arriva così ad asyncpg, e Postgres risponde
`syntax error at or near ":"`. L'errore non è a compile-time né a import-time —
esplode solo quando quella riga viene davvero eseguita, che per gli scheduler
edge-triggered voleva dire "solo quando un merchant configura l'automazione".

Costo reale: `mark_no_answer_fired` e `mark_dormant_fired` non hanno mai scritto
un'ancora, quindi i trigger "nessuna risposta" e "lead dormiente" non hanno mai
emesso un evento; `claim_handoff` non ha mai marcato un'escalation (0 righe con
`meta ? 'escalated'` su 1047 conversazioni); `save_context_summary` non ha mai
salvato niente. Sei chiamate, tutte silenziosamente morte.

La forma sicura è `CAST(:anchor AS text)`, che non mette i due punti a contatto
con il nome del parametro. Questo test tiene fuori la forma insicura da tutto il
backend, perché il punto debole non è il singolo call-site: è che `:x::t` si
rilegge come corretto ogni volta.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects import postgresql

# Un parametro nominato incollato a un cast Postgres: `:nome::tipo`.
UNBOUND_CAST = re.compile(r"(?<![:\w$]):[a-zA-Z_]\w*::")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".venv", "__pycache__", ".ruff_cache", "node_modules", "tests"}


def _source_files() -> list[Path]:
    return [
        p
        for p in BACKEND_ROOT.rglob("*.py")
        if not SKIP_DIRS & set(p.relative_to(BACKEND_ROOT).parts)
    ]


def test_sqlalchemy_non_lega_un_parametro_seguito_da_doppio_due_punti() -> None:
    """Il motivo per cui il resto del file esiste, reso esplicito."""
    dialect = postgresql.asyncpg.dialect()

    rotto = text("SELECT to_jsonb(:anchor::text)").compile(dialect=dialect)
    assert list(rotto.params) == [], (
        "se SQLAlchemy ora lega `:anchor::text`, questa guardia può essere rimossa"
    )
    assert ":anchor" in str(rotto)  # il letterale arriva intatto al database

    sano = text("SELECT to_jsonb(CAST(:anchor AS text))").compile(dialect=dialect)
    assert list(sano.params) == ["anchor"]
    assert "$1" in str(sano)


def test_nessun_parametro_incollato_a_un_cast_nel_backend() -> None:
    colpevoli: list[str] = []
    for path in _source_files():
        for numero, riga in enumerate(path.read_text().splitlines(), start=1):
            if UNBOUND_CAST.search(riga):
                rel = path.relative_to(BACKEND_ROOT)
                colpevoli.append(f"{rel}:{numero}: {riga.strip()}")

    assert not colpevoli, (
        "parametro non legato: `:nome::tipo` non è un bind param per SQLAlchemy, "
        "usare `CAST(:nome AS tipo)`.\n" + "\n".join(colpevoli)
    )
