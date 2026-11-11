#!/bin/sh
# Container entry point. With a database configured, apply migrations first (idempotent),
# then serve on the platform's port. Without one, the image serves the synthetic preview.
set -eu
if [ -n "${TAILWATCH_DATABASE_URL:-}" ]; then
  alembic upgrade head
fi
exec uvicorn tailwatch.product.server:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips='*'
