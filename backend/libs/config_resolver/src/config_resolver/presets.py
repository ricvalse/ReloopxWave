"""Curated persona presets — a guided shortcut over the raw `bot.*` knobs.

These are *not* new config keys. A preset is just a bundle of values for the
existing `bot.formality` / `bot.verbosity` / `bot.emoji_policy` / `bot.tone`
keys; applying one is a normal `PUT /{merchant_id}/overrides`. The frontend
reads this catalog to render the picker, and the suggested-rules library to
let merchants click-to-append `bot.do_phrases` / `bot.dont_phrases`.

Single source of truth so the picker, the playground, and the seed script all
agree. Exposed read-only via `GET /bot-config/tone-presets` and
`GET /bot-config/suggested-rules`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class TonePreset(BaseModel):
    """A named persona shortcut. `values` are dotted config keys → values,
    ready to merge into a merchant's override bag."""

    id: str
    label: str
    description: str
    values: dict[str, Any]


class SuggestedRules(BaseModel):
    """Curated do / don't phrase library. Each phrase ≤200 chars to match the
    `bot.do_phrases` / `bot.dont_phrases` field bounds."""

    do: list[str]
    dont: list[str]


TONE_PRESETS: list[TonePreset] = [
    TonePreset(
        id="friendly",
        label="Cordiale e diretto",
        description="Amichevole e caloroso, dà del tu, emoji con misura. Adatto a brand vicini al cliente.",
        values={
            "bot.formality": "dai-del-tu",
            "bot.verbosity": "equilibrato",
            "bot.emoji_policy": "sobrio",
            "bot.tone": "amichevole e caloroso",
        },
    ),
    TonePreset(
        id="professional",
        label="Professionale e formale",
        description="Cortese e strutturato, dà del Lei, niente emoji. Adatto a servizi, B2B, settori tecnici.",
        values={
            "bot.formality": "dai-del-lei",
            "bot.verbosity": "equilibrato",
            "bot.emoji_policy": "mai",
            "bot.tone": "professionale e cortese",
        },
    ),
    TonePreset(
        id="casual",
        label="Informale e giovane",
        description="Linguaggio diretto e brioso, dà del tu, emoji libere. Adatto a brand giovani e social.",
        values={
            "bot.formality": "dai-del-tu",
            "bot.verbosity": "conciso",
            "bot.emoji_policy": "libero",
            "bot.tone": "informale e giovane",
        },
    ),
    TonePreset(
        id="luxury",
        label="Elegante e raffinato",
        description="Tono esclusivo e curato, dà del Lei, emoji rare. Il cliente si sente coccolato.",
        values={
            "bot.formality": "dai-del-lei",
            "bot.verbosity": "dettagliato",
            "bot.emoji_policy": "sobrio",
            "bot.tone": "elegante ed esclusivo",
        },
    ),
    TonePreset(
        id="concise",
        label="Essenziale e veloce",
        description="Va dritto al punto come una persona su WhatsApp: niente convenevoli, niente emoji.",
        values={
            "bot.formality": "dai-del-tu",
            "bot.verbosity": "conciso",
            "bot.emoji_policy": "mai",
            "bot.tone": "conciso e diretto",
        },
    ),
]


SUGGESTED_RULES = SuggestedRules(
    do=[
        "Usa sempre un tono cordiale e disponibile",
        "Rispondi sempre in italiano",
        "Conferma i dati con il cliente prima di procedere con una prenotazione",
        "Chiedi chiarimenti quando la richiesta non è chiara",
        "Proponi un appuntamento o un contatto quando il cliente è interessato",
        "Ringrazia il cliente per averci scritto",
    ],
    dont=[
        "Non offrire sconti o promozioni non autorizzati",
        "Non menzionare i concorrenti",
        "Non dare informazioni su prodotti o servizi non presenti nel catalogo",
        "Non fare promesse su tempistiche che non puoi garantire",
        "Non condividere dati personali di altri clienti",
        "Non inventare informazioni: se non sai, passa a un operatore",
    ],
)
