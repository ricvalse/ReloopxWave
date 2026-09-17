"""Phone normalisation shared by the WhatsApp and CRM ingestion paths."""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D+")

# Italian mobile numbers in national format: 10 digits, always starting with
# "3" (3xx xxx xxxx). Repairing exactly this shape is safe precisely because
# it's narrow: a number already carrying a country code is 11+ digits (or 10
# digits not starting with "3"), so this branch can't misfire on an
# already-correct WhatsApp identity — see `normalize_phone`.
_IT_MOBILE_NATIONAL = re.compile(r"^3\d{9}$")


def normalize_phone(raw: str | None) -> str | None:
    """Normalise a phone number to the digits-only form WhatsApp identities use.

    360dialog delivers `from` as bare digits with country code ("39333...")
    while GHL sends whatever the agency typed into the contact's phone field —
    E.164 ("+39 333 123...") most of the time, but also plain national format
    ("333 123...") when nobody bothered with the prefix. Reducing both to
    digits (dropping a leading international "00") lets the two sources key
    the same lead — *when* the GHL side did include a country code.

    When it didn't: a GHL contact entered as "333 123 4567" and the same
    person texting in as "393331234567" used to produce two different digit
    strings, hence two different Lead/Conversation rows for the same real
    person — the automation's touch landed on one thread, the reply on the
    other, and the flow looked "dead" (Ghilea incident, 2026-09-16 — see ADR
    0035). We can't repair an arbitrary local number without knowing the
    merchant's country, but an Italian mobile in national format is
    unambiguous (`_IT_MOBILE_NATIONAL`: exactly 10 digits, starts with "3" —
    no other supported source ever produces that exact shape), so it gets the
    "39" prefix restored. Anything else without a recognisable prefix still
    passes through unrepaired, same as before.

    Returns None when fewer than 6 digits survive — not a usable number.
    """
    if not raw:
        return None
    digits = _NON_DIGITS.sub("", str(raw))
    if digits.startswith("00"):
        digits = digits[2:]
    if _IT_MOBILE_NATIONAL.match(digits):
        digits = "39" + digits
    return digits if len(digits) >= 6 else None
