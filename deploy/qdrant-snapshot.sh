#!/usr/bin/env bash
#
# Qdrant snapshots, on Droplet A. Install as a daily cron:
#
#   ln -s /opt/cdip/deploy/qdrant-snapshot.sh /etc/cron.daily/qdrant-snapshot
#
# Qdrant's own snapshot API is used rather than a disk image because it is
# CONSISTENT: a filesystem copy of a running database may catch it mid-write.
#
# This is insurance, not a lifeline. Everything in Qdrant is derived — Postgres
# holds every chunk's text and every reference. Losing it costs an embedding
# run (POST /projects/:id/documents/reindex with {"reembed": true}), not data.
# The snapshot just makes recovery minutes instead of hours and dollars.
set -euo pipefail

QDRANT="http://127.0.0.1:6333"
COLLECTION="${QDRANT_COLLECTION:-chunks}"
KEEP_DAYS=7

if ! curl -sf --max-time 10 "$QDRANT/collections/$COLLECTION" >/dev/null; then
  echo "qdrant-snapshot: collection $COLLECTION unreachable — skipping" >&2
  exit 0
fi

curl -sf -X POST "$QDRANT/collections/$COLLECTION/snapshots" >/dev/null
echo "qdrant-snapshot: created for $COLLECTION"

# Snapshots live inside the container's volume; prune through the container so
# the path is the one Qdrant actually wrote to.
docker exec "$(docker ps -qf name=qdrant)" \
  find /qdrant/snapshots -type f -name '*.snapshot' -mtime "+$KEEP_DAYS" -delete 2>/dev/null || true
