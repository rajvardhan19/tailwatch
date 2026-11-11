#!/bin/sh
# Container entry point. With a database configured, apply migrations first (idempotent),
# then serve on the platform's port. Without one, the image serves the synthetic preview.
set -eu
# Hosted databases such as Neon hand out postgresql:// (or postgres://) URLs; use psycopg 3.
case "${TAILWATCH_DATABASE_URL:-}" in
  postgresql://* | postgres://*)
    TAILWATCH_DATABASE_URL="postgresql+psycopg://${TAILWATCH_DATABASE_URL#*://}"
    export TAILWATCH_DATABASE_URL
    ;;
esac
if [ -n "${TAILWATCH_DATABASE_URL:-}" ]; then
  alembic upgrade head
fi
exec uvicorn tailwatch.product.server:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips='*'
