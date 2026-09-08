"""Il pool va dimensionato sul budget del pooler, non sul singolo processo.

Il bug, l'08/09/2026. `create_engine` aveva `pool_size=5, max_overflow=10`
cablati: quindici connessioni **per processo**. Ma il pooler Supabase in session
mode concede quindici client **in tutto**, e quel budget è diviso fra tutti i
processi insieme — i due worker uvicorn dell'API, il worker arq, e durante un
deploy anche il container vecchio che sta ancora servendo.

Bastavano tre processi fermi per occupare tutti gli slot: una connessione in
pool resta aperta anche quando è inattiva, quindi lo slot è occupato comunque.
Risultato: `alembic upgrade head` all'avvio dell'API non otteneva **una sola**
connessione, l'entrypoint moriva su `set -e`, Railway riavviava il container e
il deploy finiva in FAILED. Tre deploy di fila, e dai log sembrava un problema
di migrazione: non lo era.

Questi test tengono fermo il ragionamento, non il numero: se qualcuno rialza i
default, il conto qui sotto smette di tornare e spiega perché.
"""

from __future__ import annotations

import pytest

from db.session import create_engine
from shared import get_settings

# Quanti client concede il pooler in session mode. Non è una nostra scelta: è il
# limite del piano Supabase.
SLOT_DISPONIBILI = 15

# Processi che insistono sullo stesso pooler nel momento peggiore, cioè durante
# un deploy: i due uvicorn del container vecchio (che serve ancora), i due del
# nuovo, e il worker arq.
PROCESSI_DURANTE_UN_DEPLOY = 5

DSN_FINTO = "postgresql+asyncpg://u:p@localhost:5432/x"


def test_il_pool_a_regime_lascia_slot_liberi_per_la_migrazione() -> None:
    """A riposo i processi non devono occupare tutto il budget.

    È la condizione che mancava: senza almeno uno slot libero, la migrazione al
    boot dell'API non parte e il deploy non va a buon fine.
    """
    s = get_settings()
    occupati_a_regime = s.db_pool_size * PROCESSI_DURANTE_UN_DEPLOY
    assert occupati_a_regime < SLOT_DISPONIBILI, (
        f"{PROCESSI_DURANTE_UN_DEPLOY} processi x pool_size={s.db_pool_size} = "
        f"{occupati_a_regime} slot occupati a riposo, su {SLOT_DISPONIBILI} "
        "disponibili: alembic resterebbe senza connessione e il deploy fallirebbe."
    )
    # Margine reale, non uno slot per il rotto della cuffia.
    assert SLOT_DISPONIBILI - occupati_a_regime >= 3


def test_il_singolo_processo_non_puo_da_solo_esaurire_il_pooler() -> None:
    """Nessun processo deve poter affamare gli altri sotto picco.

    Era esattamente il vecchio comportamento: 5+10 = 15, cioè un processo solo
    poteva prendersi l'intero pooler e lasciare a secco API e migrazione.
    """
    s = get_settings()
    tetto_per_processo = s.db_pool_size + s.db_max_overflow
    assert tetto_per_processo < SLOT_DISPONIBILI, (
        f"un processo può arrivare a {tetto_per_processo} connessioni su "
        f"{SLOT_DISPONIBILI}: da solo può affamare tutti gli altri."
    )


def test_le_dimensioni_arrivano_dalle_settings_non_dal_codice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Devono restare regolabili senza toccare il codice.

    Serve per poterle rialzare il giorno in cui si passa alla porta 6543
    (transaction mode), dove il tetto è molto più alto.
    """
    monkeypatch.setenv("DB_POOL_SIZE", "4")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "1")
    # `get_settings` è `@lru_cache`: senza svuotarla si rilegge il valore
    # catturato al primo import e il test passerebbe per finta.
    get_settings.cache_clear()

    try:
        engine = create_engine(DSN_FINTO)
        # Nessuna connessione viene aperta: `create_async_engine` è pigro.
        assert engine.pool.size() == 4
    finally:
        # L'env torna a posto da solo (monkeypatch), ma la cache no: va svuotata
        # di nuovo o i test successivi ereditano il 4.
        get_settings.cache_clear()
