#!/usr/bin/env bash
#
# Deploy one machine's role. Run it ON that machine, from the repo checkout.
#
#   ./deploy/deploy.sh app       # Droplet A — api + web + qdrant (runs migrations)
#   ./deploy/deploy.sh worker    # Droplet B — worker
#   ./deploy/deploy.sh local     # the office server PC — everything, one machine
#
# ORDER MATTERS ACROSS THE TWO DROPLETS: deploy `app` first, because it runs
# the migrations. A worker started against an older schema fails its jobs;
# a worker started against a newer one is the outage you cannot roll back
# from cleanly. The `local` role has no such ordering — it is one machine and
# migrates before it starts anything.
set -euo pipefail

ROLE="${1:-}"
cd "$(dirname "$0")/.."

case "$ROLE" in
  app|worker|local) ;;
  *) echo "usage: $0 app|worker|local" >&2; exit 1 ;;
esac

COMPOSE="deploy/docker-compose.${ROLE}.yml"
ENV_FILE="deploy/.env.${ROLE}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE — copy ${ENV_FILE}.example and fill it in" >&2
  exit 1
fi

# Several values are interpolated INTO the compose file at parse time rather
# than passed to a container (APP_DOMAIN and PRIVATE_IP for app; the Postgres
# and MinIO credentials and STORAGE_DATA_DIR for local), so they have to be in
# this shell's environment too.
set -a; source "$ENV_FILE"; set +a

if [[ "$ROLE" == "local" ]]; then
  # The one value with no sane default and no way to detect it from inside a
  # container. Signed URLs are signed against the host the browser will
  # request them on, so a wrong or unedited value does not degrade the
  # uploads, it rejects every one of them with SignatureDoesNotMatch — and it
  # does so in the browser, where the API logs show nothing at all.
  if [[ -z "${LOCAL_S3_PUBLIC_ENDPOINT:-}" ||
        ( "$LOCAL_S3_PUBLIC_ENDPOINT" == "http://192.168.1.50:9000" &&
          -z "${CDIP_ALLOW_EXAMPLE_IP:-}" ) ]]; then
    cat >&2 <<MSG
LOCAL_S3_PUBLIC_ENDPOINT in $ENV_FILE is unset or still the example address.

Set it to THIS PC's address on the office network, port 9000 — the address
another laptop's browser will upload to. Read it off Windows with \`ipconfig\`
(the wifi or Ethernet adapter's IPv4 address), not from inside WSL, whose
address belongs to a virtual network no other laptop can reach.

  LOCAL_S3_PUBLIC_ENDPOINT=http://<this-pc-ip>:9000

If http://192.168.1.50:9000 genuinely is this PC, re-run with
CDIP_ALLOW_EXAMPLE_IP=1 to say so.
MSG
    exit 1
  fi
  if [[ -z "${POSTGRES_PASSWORD:-}" || -z "${LOCAL_S3_SECRET:-}" ]]; then
    echo "POSTGRES_PASSWORD and LOCAL_S3_SECRET must be set in $ENV_FILE" >&2
    exit 1
  fi
  # Created before the container starts, or Docker creates it as root and
  # MinIO cannot write to it. Then made ABSOLUTE, because a relative bind
  # mount in a compose file resolves against the FILE's directory (deploy/)
  # while this script runs from the repo root — the two would silently pick
  # different folders and the drawings would land somewhere nobody backs up.
  STORAGE_DATA_DIR="${STORAGE_DATA_DIR:-./data/storage}"
  mkdir -p "$STORAGE_DATA_DIR"
  STORAGE_DATA_DIR="$(cd "$STORAGE_DATA_DIR" && pwd)"
  export STORAGE_DATA_DIR
  echo "storage: $STORAGE_DATA_DIR"
fi

step() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }

step "Fetching $(git rev-parse --abbrev-ref HEAD)"
git pull --ff-only

step "Building images"
docker compose -f "$COMPOSE" build

if [[ "$ROLE" == "app" || "$ROLE" == "local" ]]; then
  # Qdrant must be up before the api's health check can pass, and the
  # migration container needs nothing but the database — so it runs first and
  # alone. `run --rm` gives a non-zero exit if a migration fails, which `set
  # -e` turns into a stopped deploy rather than a half-migrated system serving
  # traffic.
  step "Running database migrations"
  docker compose -f "$COMPOSE" run --rm --no-deps api \
    npx prisma migrate deploy --schema apps/api/prisma/schema.prisma
fi

step "Starting containers"
docker compose -f "$COMPOSE" up -d --remove-orphans

step "Waiting for health"
if [[ "$ROLE" == "app" || "$ROLE" == "local" ]]; then
  for _ in $(seq 1 30); do
    if docker compose -f "$COMPOSE" ps api --format json 2>/dev/null | grep -q '"Health":"healthy"'; then
      echo "api is healthy"
      break
    fi
    sleep 5
  done
  # The health endpoint checks Postgres, Redis and Qdrant, so this one line is
  # the whole dependency chain reporting in.
  docker compose -f "$COMPOSE" exec -T api \
    node -e "fetch('http://localhost:4000/health').then(r=>r.text()).then(console.log)" || true
else
  docker compose -f "$COMPOSE" ps
fi

# Old images accumulate fast when every deploy rebuilds a node_modules layer.
step "Pruning old images"
docker image prune -f --filter "until=168h" >/dev/null

step "Done — $(git rev-parse --short HEAD)"
