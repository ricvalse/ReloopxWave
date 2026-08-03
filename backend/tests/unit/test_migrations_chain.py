"""Invarianti della catena Alembic, verificate senza toccare un database.

Esistono perché il modo in cui queste rompono è **al deploy, in produzione**:
l'API applica `alembic upgrade head` all'avvio (`api-entrypoint.sh`), quindi una
catena malformata non fallisce in CI né in locale — fallisce quando il container
parte, e il servizio resta sulla build precedente senza che nulla lo dica.

Il caso reale (2026-08-03): la revision `0047_attribution_profiles_outcomes` era
lunga 34 caratteri e `internal.alembic_version.version_num` è `VARCHAR(32)` —
alembic la crea così e non la allarga mai. La migrazione girava, poi lo stamp
finale esplodeva con `StringDataRightTruncationError` e l'intera transazione
tornava indietro. L'API non deployava da cinque giorni e i tre commit successivi
non erano mai arrivati in produzione.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "libs/db/src/db/migrations/versions"

# Larghezza di `internal.alembic_version.version_num`. È il default di alembic
# (`String(32)`), e `env.py` non lo sovrascrive con `version_table_column_type`.
# Se un giorno lo facesse, il vincolo va allineato qui *e* la colonna esistente
# va allargata a mano: alembic non altera una tabella di versione già creata.
VERSION_NUM_MAX_LEN = 32

_REVISION_RE = re.compile(r"^revision[^=]*=\s*[\"']([^\"']+)", re.M)
_DOWN_RE = re.compile(r"^down_revision[^=]*=\s*[\"']([^\"']+)", re.M)


def _load() -> dict[str, tuple[str, str | None]]:
    """{revision: (filename, down_revision)} per ogni script di migrazione."""
    out: dict[str, tuple[str, str | None]] = {}
    for path in sorted(VERSIONS_DIR.glob("[0-9]*.py")):
        text = path.read_text()
        rev = _REVISION_RE.search(text)
        assert rev, f"{path.name}: nessun `revision = ...` trovato"
        down = _DOWN_RE.search(text)
        out[rev.group(1)] = (path.name, down.group(1) if down else None)
    return out


def test_revision_ids_fit_the_version_column() -> None:
    """Un id più lungo della colonna fa fallire lo *stamp*, non la migrazione.

    L'errore arriva dopo che il DDL è passato, quindi il messaggio parla di una
    stringa troppo lunga e non della migrazione che la stava scrivendo.
    """
    too_long = {
        rev: (name, len(rev))
        for rev, (name, _) in _load().items()
        if len(rev) > VERSION_NUM_MAX_LEN
    }
    assert not too_long, (
        f"revision id oltre {VERSION_NUM_MAX_LEN} caratteri: {too_long}. "
        "Accorcia l'identificativo (e il down_revision che lo cita)."
    )


def test_single_head() -> None:
    """Due teste fanno fallire `alembic upgrade head` come ambiguo.

    È l'esito tipico di un merge in cui due rami hanno aggiunto una migrazione
    ciascuno partendo dalla stessa base.
    """
    chain = _load()
    downs = {down for _, down in chain.values() if down}
    heads = sorted(rev for rev in chain if rev not in downs)
    assert len(heads) == 1, f"attesa una sola testa, trovate: {heads}"


def test_chain_is_connected() -> None:
    """Ogni `down_revision` risolve, e c'è esattamente una radice."""
    chain = _load()
    orphans = {rev: down for rev, (_, down) in chain.items() if down and down not in chain}
    assert not orphans, f"down_revision che non esistono: {orphans}"

    roots = sorted(rev for rev, (_, down) in chain.items() if down is None)
    assert len(roots) == 1, f"attesa una sola radice, trovate: {roots}"
