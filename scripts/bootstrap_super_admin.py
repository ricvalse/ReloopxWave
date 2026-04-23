#!/usr/bin/env python
"""Create (or refresh the claims of) a super_admin user.

Runs against the configured Supabase project using the service_role key —
so treat the output as sensitive and do not commit the printed credentials.

Usage
-----
    # Local / CI:
    uv run python scripts/bootstrap_super_admin.py --email me@example.com

    # Railway (prod/staging):
    railway run --service api \
        uv run python scripts/bootstrap_super_admin.py \
            --email ops@reloop.ai --password "$TEMP_PW"

If `--password` is omitted an invite email is sent instead so the user picks
their own password via the magic link.

This script does NOT modify the `users` table — the Supabase auth row alone is
enough for the JWT hook to mint a session with `role=super_admin` and
`tenant_id=<platform>`. Rows in `public.users` are optional for super_admin
because every RLS policy grants bypass via the `super_admin_bypass_*`
policies added in migration 0005.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from integrations import SupabaseAdminClient
from shared import PLATFORM_TENANT_ID, get_settings


async def main(*, email: str, password: str | None) -> int:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print(
            "ERROR: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY must be set.",
            file=sys.stderr,
        )
        return 2

    client = SupabaseAdminClient(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    try:
        if password:
            user = await client.create_user(
                email=email,
                tenant_id=PLATFORM_TENANT_ID,
                merchant_id=None,
                role="super_admin",
                password=password,
            )
            print(f"Created super_admin user id={user.id} email={user.email}")
            print("Password set from CLI — rotate after first login.")
        else:
            user = await client.invite_user_by_email(
                email=email,
                tenant_id=PLATFORM_TENANT_ID,
                merchant_id=None,
                role="super_admin",
            )
            print(f"Invite sent to {user.email} (user id={user.id}).")
            print("app_metadata.role=super_admin, tenant_id=platform has been set.")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--email", required=True, help="Super-admin email address")
    parser.add_argument(
        "--password",
        default=None,
        help="Optional initial password. If omitted, an invite email is sent.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(email=args.email, password=args.password)))
