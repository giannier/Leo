#!/usr/bin/env bash
# Encrypted daily backup of Leo (TASK-026): the database, which includes the
# files (photos, receipts, documents), so there is one thing to back up.
#
#   deploy/backup.sh                     # from the repo root on the VPS
#   15 4 * * * cd /opt/leo && deploy/backup.sh >> /var/log/leo-backup.log 2>&1
#
# Each run writes one file to $BACKUP_DIR, encrypted with BACKUP_PASSPHRASE
# (AES-256, openssl pbkdf2), and deletes those older than $BACKUP_KEEP_DAYS:
#   leo-db-<stamp>.dump.enc     pg_dump custom format (restore with restore.sh)
# Copy $BACKUP_DIR off the VPS as well (see RUNBOOK.md): a backup on the same
# disk does not survive losing the disk.
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=deploy/.env.prod
COMPOSE=(docker compose -f deploy/docker-compose.prod.yml --env-file "$ENV_FILE")

# One key at a time: the env file is not valid bash (an unquoted `EMAIL_FROM=Leo <...>` breaks
# `source`), and only these four are needed. Nothing is echoed.
env_value() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }
BACKUP_PASSPHRASE="$(env_value BACKUP_PASSPHRASE)"
POSTGRES_USER="$(env_value POSTGRES_USER)"
POSTGRES_DB="$(env_value POSTGRES_DB)"
export BACKUP_PASSPHRASE

: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is empty in $ENV_FILE}"
: "${POSTGRES_USER:?POSTGRES_USER is empty in $ENV_FILE}"
: "${POSTGRES_DB:?POSTGRES_DB is empty in $ENV_FILE}"
BACKUP_DIR="${BACKUP_DIR:-$(env_value BACKUP_DIR)}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/leo}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

encrypt() {
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE
}

db_file="$BACKUP_DIR/leo-db-$STAMP.dump.enc"
# `-Z 1`: most of what is in the dump is photos, PDFs and video, which are already
# compressed, so the default level spends minutes of CPU to save almost nothing.
"${COMPOSE[@]}" exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -Z 1 --no-owner \
  | encrypt > "$db_file.partial"
mv "$db_file.partial" "$db_file"

chmod 600 "$db_file"
find "$BACKUP_DIR" -name 'leo-*.enc' -mtime +"$BACKUP_KEEP_DAYS" -delete

# How much of the database is files: the number to watch as the business grows.
stored="$("${COMPOSE[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c \
  "select count(*) || ' files, ' || pg_size_pretty(coalesce(sum(size_bytes), 0)) from stored_objects" \
  2>/dev/null || echo "n/a")"

echo "$(date -u +%FT%TZ) backup ok: $(du -h "$db_file" | cut -f1) (stored: $stored)"
