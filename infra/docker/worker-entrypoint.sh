#!/usr/bin/env sh
# Worker container entrypoint.
#
# We deliberately do NOT run migrations here — the api container owns the
# migration step. If the worker boots before the api has finished migrating,
# its first jobs will hit `relation does not exist`; ARQ retries with backoff
# so this self-heals within seconds.

set -e

echo "▶ arq workers.settings.WorkerSettings"
exec arq workers.settings.WorkerSettings
