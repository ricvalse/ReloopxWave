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


# Secondo tranello, trovato correggendo il primo: `CAST(:reason AS text)` è
# sintatticamente valido, ma se `:reason` compare **anche** altrove nella stessa
# query SQLAlchemy emette un solo placeholder per nome — `$1` in entrambi i punti
# — e Postgres deve dedurne il tipo due volte. Quando i due contesti non
# concordano (qui `handoff_reason` è varchar e il CAST forza text) risponde
# `AmbiguousParameterError: inconsistent types deduced for parameter $1`.
#
# Anche questo esplode solo in esecuzione, e anche questo non lo vede nessun test
# unitario: `claim_handoff` è rimasto rotto per mesi con `meta ? 'escalated'` a
# zero su 1047 conversazioni. La cura è legare il valore due volte con due nomi.

CAST_PARAM = re.compile(r"CAST\(\s*:([a-zA-Z_]\w*)\s+AS\b", re.IGNORECASE)
NAMED_PARAM = re.compile(r"(?<![:\w$]):([a-zA-Z_]\w*)")
# Blocchi `text("""...""")` o `text("...")`.
SQL_BLOCK = re.compile(r'text\(\s*(?:"""(.*?)"""|"([^"]*)")', re.DOTALL)


def test_nessun_parametro_castato_e_riusato_altrove() -> None:
    colpevoli: list[str] = []
    for path in _source_files():
        testo = path.read_text()
        for match in SQL_BLOCK.finditer(testo):
            sql = match.group(1) or match.group(2) or ""
            nomi = NAMED_PARAM.findall(sql)
            castati = set(CAST_PARAM.findall(sql))
            ripetuti = {n for n in nomi if nomi.count(n) > 1}
            for nome in sorted(ripetuti & castati):
                riga = testo[: match.start()].count("\n") + 1
                rel = path.relative_to(BACKEND_ROOT)
                colpevoli.append(f"{rel}:{riga}: `:{nome}` è castato e riusato nella stessa query")

    assert not colpevoli, (
        "un parametro castato non può comparire anche altrove nella stessa query: "
        "SQLAlchemy emette un solo placeholder e Postgres ne deduce due tipi in "
        "conflitto (AmbiguousParameterError). Legare il valore a un secondo nome.\n"
        + "\n".join(colpevoli)
    )
