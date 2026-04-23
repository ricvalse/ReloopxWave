"""Well-known IDs and role vocabulary shared across the backend.

Any value that needs to match between Python code and SQL migrations belongs
here so drift becomes a grep rather than a bug report.
"""

from __future__ import annotations

from uuid import UUID

# The seeded tenant super_admin users belong to. Created in migration
# 0005_super_admin_support; keep this constant in sync with that file.
PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

# Role vocabulary — the values we store in app_metadata.role and accept
# in JWT claims. Kept as a frozenset so callers that need "one of these"
# checks don't rebuild the set on every request.
KNOWN_ROLES: frozenset[str] = frozenset(
    {
        "super_admin",
        "agency_admin",
        "agency_user",
        "merchant_admin",
        "merchant_user",
    }
)

SUPER_ADMIN_ROLE = "super_admin"
