"""Well-known IDs and role vocabulary shared across the backend.

Any value that needs to match between Python code and SQL migrations belongs
here so drift becomes a grep rather than a bug report.
"""

from __future__ import annotations

# Role vocabulary — kept intentionally tiny. Wave Marketing operates the
# whole platform; their staff are `agency_admin`. Every other user logs into
# the merchant portal as `merchant_user` scoped to a single merchant via RLS.
KNOWN_ROLES: frozenset[str] = frozenset(
    {
        "agency_admin",
        "merchant_user",
    }
)
