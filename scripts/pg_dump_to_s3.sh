#!/usr/bin/env bash
set -euo pipefail

# Daily PostgreSQL backup script streaming gzipped pg_dump directly to S3.

if [[ -z "${BACKUP_BUCKET:-}" ]]; then
  echo "Error: BACKUP_BUCKET environment variable is required." >&2
  exit 1
fi

DATE_STR=$(date -u +%F)
DEST="s3://${BACKUP_BUCKET}/postgres/${DATE_STR}.sql.gz"

if ! docker exec openeval-postgres pg_isready -U openeval -d openeval >/dev/null 2>&1; then
  echo "PostgreSQL is not ready or container is not running; skipping backup." >&2
  exit 0
fi

echo "Starting PostgreSQL backup to ${DEST}..."
docker exec openeval-postgres pg_dump -U openeval openeval | gzip | aws s3 cp - "${DEST}"
echo "Backup successfully written to ${DEST}."
