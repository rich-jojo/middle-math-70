#!/usr/bin/env sh
set -eu

OUT="${1:-backups/mm70-$(date +%Y%m%d-%H%M%S).dump}"
mkdir -p "$(dirname "$OUT")"
docker-compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$OUT"
printf '%s\n' "$OUT"
