#!/usr/bin/env sh
set -eu

if [ "${1:-}" = "" ]; then
  echo "usage: scripts/restore.sh backups/file.dump" >&2
  exit 2
fi

docker-compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < "$1"
