#!/usr/bin/env bash
set -euo pipefail

# PostgreSQL disaster recovery restore script from S3 backup.
# Usage: ./scripts/pg_restore_from_s3.sh s3://<bucket>/postgres/YYYY-MM-DD.sql.gz

if [[ $# -lt 1 || -z "$1" ]]; then
  echo "Usage: $0 s3://<bucket>/postgres/YYYY-MM-DD.sql.gz" >&2
  exit 1
fi

BACKUP_URI="$1"

if [[ ! "$BACKUP_URI" =~ ^s3://.*\.sql\.gz$ ]]; then
  echo "Error: Argument must be an S3 URI ending with .sql.gz" >&2
  exit 1
fi

echo "============================================================"
echo "  WARNING: PostgreSQL Database Restore"
echo "  Source: ${BACKUP_URI}"
echo "  This will overwrite active data in Postgres!"
echo "============================================================"

if [[ "${FORCE:-0}" != "1" ]]; then
  read -r -p "Type 'RESTORE' to confirm: " CONFIRM
  if [[ "$CONFIRM" != "RESTORE" ]]; then
    echo "Restore cancelled."
    exit 0
  fi
fi

echo "1. Stopping application container..."
docker compose -f docker-compose.prod.yml stop app 2>/dev/null || docker stop openeval-studio 2>/dev/null || true

echo "2. Streaming and applying backup from S3..."
aws s3 cp "${BACKUP_URI}" - | gunzip | docker exec -i openeval-postgres psql -U openeval -d openeval

echo "3. Restarting application container..."
docker compose -f docker-compose.prod.yml start app 2>/dev/null || docker start openeval-studio 2>/dev/null || true

echo "Restore from ${BACKUP_URI} completed successfully."
