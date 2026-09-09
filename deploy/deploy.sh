#!/usr/bin/env bash
#
# Deploy one Droplet's role. Run it ON that Droplet, from the repo checkout.
#
#   ./deploy/deploy.sh app       # Droplet A — api + web + qdrant (runs migrations)
#   ./deploy/deploy.sh worker    # Droplet B — worker
#
# ORDER MATTERS ACROSS THE TWO MACHINES: deploy `app` first, because it runs
# the migrations. A worker started against an older schema fails its jobs;
# a worker started against a newer one is the outage you cannot roll back
# from cleanly.
set -euo pipefail

ROLE="${1:-}"
cd "$(dirname "$0")/.."

if [[ "$ROLE" != "app" && "$ROLE" != "worker" ]]; then
  echo "usage: $0 app|worker" >&2
  exit 1
fi

COMPOSE="deploy/docker-compose.${ROLE}.yml"
ENV_FILE="deploy/.env.${ROLE}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE — copy ${ENV_FILE}.example and fill it in" >&2
  exit 1
fi

# The compose file reads APP_DOMAIN and PRIVATE_IP for the app role, and those
# are interpolated at parse time rather than passed to a container, so they
# have to be in this shell's environment too.
set -a; source "$ENV_FILE"; set +a

step() { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }

step "Fetching $(git rev-parse --abbrev-ref HEAD)"
git pull --ff-only

step "Building images"
docker compose -f "$COMPOSE" build

if [[ "$ROLE" == "app" ]]; then
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
if [[ "$ROLE" == "app" ]]; then
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
