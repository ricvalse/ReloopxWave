"""Phone normalisation shared by the WhatsApp and CRM ingestion paths."""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D+")


def normalize_phone(raw: str | None) -> str | None:
    """Normalise a phone number to the digits-only form WhatsApp identities use.

    360dialog delivers `from` as bare digits with country code ("39333...")
    while GHL sends E.164-ish strings ("+39 333 123..."); reducing both to
    digits (dropping a leading international "00") lets the two sources key
    the same lead. Numbers without a country prefix can't be repaired here
    (no per-merchant country default in V1) and pass through as-is. Returns
    None when fewer than 6 digits survive — not a usable number.
    """
    if not raw:
        return None
    digits = _NON_DIGITS.sub("", str(raw))
    if digits.startswith("00"):
        digits = digits[2:]
    return digits if len(digits) >= 6 else None
