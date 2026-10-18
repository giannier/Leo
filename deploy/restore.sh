#!/usr/bin/env bash
# Restore a backup made by deploy/backup.sh (TASK-026).
#
#   deploy/restore.sh /var/backups/leo/leo-db-<stamp>.dump.enc [/var/backups/leo/leo-files-<stamp>.tar.gz.enc]
#
# Replaces the database (and, if given, the files) with the backup's. It stops
# the API and the worker while it works and starts them again at the end.
# Asks for confirmation unless RESTORE_YES=1.
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=deploy/.env.prod
COMPOSE=(docker compose -f deploy/docker-compose.prod.yml --env-file "$ENV_FILE")

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is empty in $ENV_FILE}"

db_file="${1:?usage: restore.sh <db backup> [files backup]}"
files_file="${2:-}"

decrypt() {
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in "$1"
}

# Fails here, before touching anything, if the passphrase or the file is wrong.
# (`head` closes the pipe early on purpose; with pipefail that is not an error.)
magic="$(decrypt "$db_file" 2>/dev/null | head -c 5 || true)"
[ "$magic" = "PGDMP" ] || { echo "cannot decrypt $db_file (wrong passphrase?)" >&2; exit 1; }

if [ "${RESTORE_YES:-0}" != "1" ]; then
  read -r -p "This REPLACES the production data with $db_file. Type 'restaurar' to go on: " answer
  [ "$answer" = "restaurar" ] || { echo "cancelled"; exit 1; }
fi

"${COMPOSE[@]}" stop backend worker

decrypt "$db_file" | "${COMPOSE[@]}" exec -T postgres \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --single-transaction

if [ -n "$files_file" ]; then
  "${COMPOSE[@]}" stop minio
  decrypt "$files_file" | docker run --rm -i -v leo_minio_data:/data alpine:3.20 \
    sh -c 'find /data -mindepth 1 -delete && tar -C /data -xzf -'
  "${COMPOSE[@]}" start minio
fi

"${COMPOSE[@]}" up -d backend worker
echo "restored from $db_file${files_file:+ and $files_file}"
