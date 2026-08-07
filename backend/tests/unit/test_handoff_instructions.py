"""Istruzioni di handoff configurabili nel prompt (ADR 0026).

Prima i criteri erano tre, cablati in `orchestrator._ACTION_SNIPPETS`, e l'unica
eccezione era la nota sui media: correggere un falso positivo in un settore
specifico richiedeva una modifica al codice, che poi valeva per tutti i merchant.
"""

from ai_core.orchestrator import (
    HandoffPrompt,
    normalize_action_kind,
    render_schema_hint,
)


def _action_block(hint: str) -> str:
    """Lo snippet dell'azione, dall'intestazione fino all'azione successiva."""
    start = hint.index('- "handoff_human"')
    return hint[start : hint.index('- "none"', start)]


# --------------------------------------------------------------------------- #
# Default: nessuna configurazione = prompt storico
# --------------------------------------------------------------------------- #
def test_default_keeps_the_three_historical_criteria_in_prose():
    block = _action_block(render_schema_hint(None))
    assert (
        "quando l'utente è arrabbiato, minaccia reclami/azioni legali, "
        "o chiede esplicitamente una persona." in block
    )
    # Nessun elenco puntato finché il merchant non configura nulla.
    assert "*" not in block


def test_no_config_and_empty_config_render_identically():
    assert render_schema_hint(None) == render_schema_hint(None, handoff=HandoffPrompt())


# --------------------------------------------------------------------------- #
# extend / replace
# --------------------------------------------------------------------------- #
def test_extend_appends_merchant_criteria_to_the_defaults():
    cfg = HandoffPrompt(criteria=("chiede un preventivo sopra 5.000 €",))
    block = _action_block(render_schema_hint(None, handoff=cfg))
    assert "* l'utente è arrabbiato" in block
    assert "* chiede un preventivo sopra 5.000 €" in block


def test_replace_drops_the_defaults():
    cfg = HandoffPrompt(mode="replace", criteria=("parla di un ordine già spedito",))
    block = _action_block(render_schema_hint(None, handoff=cfg))
    assert "* parla di un ordine già spedito" in block
    # Il caso che motiva `replace`: settori in cui la rabbia è la normalità e i
    # default produrrebbero un handoff a ogni conversazione. Si controllano i
    # punti elenco, non tutto il blocco: "cliente_arrabbiato" resta come esempio
    # nel payload, che è un'altra cosa.
    bullets = [ln for ln in block.splitlines() if ln.strip().startswith("*")]
    assert bullets == ["    * parla di un ordine già spedito"]


def test_replace_without_criteria_removes_the_action_entirely():
    """Nessun criterio = nessuna condizione per alzare la mano.

    Offrire l'azione senza dire quando usarla lascerebbe la decisione al modello,
    che è esattamente ciò che questa configurazione vuole evitare.
    """
    hint = render_schema_hint(None, handoff=HandoffPrompt(mode="replace"))
    assert "handoff_human" not in hint


# --------------------------------------------------------------------------- #
# Eccezioni (anti falso positivo)
# --------------------------------------------------------------------------- #
def test_exclusions_render_as_a_negative_block():
    cfg = HandoffPrompt(
        exclusions=(
            "dice «voglio parlare con qualcuno» prima di aver dato il budget",
            "usa la parola «reclamo», che nel nostro settore è tecnica",
        )
    )
    hint = render_schema_hint(None, handoff=cfg)
    assert "QUANDO **NON** PASSARE A UN OPERATORE" in hint
    assert "- dice «voglio parlare con qualcuno» prima di aver dato il budget" in hint
    assert "- usa la parola «reclamo», che nel nostro settore è tecnica" in hint


def test_no_exclusions_block_when_none_configured():
    assert "QUANDO **NON** PASSARE" not in render_schema_hint(None)


# --------------------------------------------------------------------------- #
# Handoff spento
# --------------------------------------------------------------------------- #
def test_disabled_removes_the_action_and_forbids_promising_an_operator():
    """Il modello deve SAPERE che non c'è nessun operatore.

    Prima `handoff.enabled=False` filtrava l'azione solo a valle: il modello
    continuava a scrivere «ti passo un collega», la frase partiva davvero verso
    il cliente e solo l'azione veniva scartata — il cliente restava ad aspettare
    una persona che non sarebbe mai arrivata.
    """
    hint = render_schema_hint(None, handoff=HandoffPrompt(enabled=False))
    assert "handoff_human" not in hint
    assert "PASSAGGIO A OPERATORE NON DISPONIBILE" in hint
    assert "Non promettere MAI" in hint


def test_disabled_note_absent_when_enabled():
    assert "NON DISPONIBILE" not in render_schema_hint(None)


# --------------------------------------------------------------------------- #
# Nome legacy dell'azione
# --------------------------------------------------------------------------- #
def test_legacy_action_kind_is_normalized():
    """`escalate_human` resta accettato in lettura (allowlist già salvate)."""
    assert normalize_action_kind("escalate_human") == "handoff_human"
    assert normalize_action_kind("handoff_human") == "handoff_human"
    assert normalize_action_kind("book_slot") == "book_slot"


def test_media_note_never_references_an_action_the_model_cannot_emit():
    """Con l'handoff spento la nota MEDIA non deve più nominarlo.

    Era il caso in cui il prompt istruiva il modello su un'azione assente
    dall'enum.
    """
    hint = render_schema_hint(None, handoff=HandoffPrompt(enabled=False))
    assert "MEDIA:" in hint
    assert "handoff_human" not in hint
