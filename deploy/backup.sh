#!/usr/bin/env bash
# Encrypted daily backup of Leo (TASK-026): the database and the files (MinIO).
#
#   deploy/backup.sh                     # from the repo root on the VPS
#   15 4 * * * cd /opt/leo && deploy/backup.sh >> /var/log/leo-backup.log 2>&1
#
# Each run writes two files to $BACKUP_DIR, encrypted with BACKUP_PASSPHRASE
# (AES-256, openssl pbkdf2), and deletes those older than $BACKUP_KEEP_DAYS:
#   leo-db-<stamp>.dump.enc     pg_dump custom format (restore with restore.sh)
#   leo-files-<stamp>.tar.gz.enc  the MinIO volume
# Copy $BACKUP_DIR off the VPS as well (see RUNBOOK.md): a backup on the same
# disk does not survive losing the disk.
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=deploy/.env.prod
COMPOSE=(docker compose -f deploy/docker-compose.prod.yml --env-file "$ENV_FILE")

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is empty in $ENV_FILE}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/leo}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

encrypt() {
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE
}

db_file="$BACKUP_DIR/leo-db-$STAMP.dump.enc"
"${COMPOSE[@]}" exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner \
  | encrypt > "$db_file.partial"
mv "$db_file.partial" "$db_file"

files_file="$BACKUP_DIR/leo-files-$STAMP.tar.gz.enc"
docker run --rm -v leo_minio_data:/data:ro alpine:3.20 tar -C /data -czf - . \
  | encrypt > "$files_file.partial"
mv "$files_file.partial" "$files_file"

chmod 600 "$db_file" "$files_file"
find "$BACKUP_DIR" -name 'leo-*.enc' -mtime +"$BACKUP_KEEP_DAYS" -delete

echo "$(date -u +%FT%TZ) backup ok: $(du -h "$db_file" | cut -f1) db, $(du -h "$files_file" | cut -f1) files"
