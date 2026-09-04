# The API, built from the REPOSITORY ROOT.
#
#   docker build -f apps/api/Dockerfile -t cdip-api .
#
# The context is the root, not apps/api, because the api imports @cdip/shared
# as an npm workspace: workspaces resolve from the root package.json, and
# @cdip/shared is consumed as BUILT javascript. A build starting inside
# apps/api cannot see it.

FROM node:22-slim AS build

WORKDIR /app

# Manifests first, so a change to source code does not re-run npm ci.
COPY package.json package-lock.json ./
COPY packages/shared/package.json packages/shared/
COPY apps/api/package.json apps/api/
COPY apps/web/package.json apps/web/
RUN npm ci

COPY packages/shared packages/shared
COPY apps/api apps/api

# Builds packages/shared FIRST, then the api — the workspace order matters and
# the api's own build script depends on it.
RUN npm run build --workspace @cdip/shared --workspace @cdip/api \
 && npx prisma generate --schema apps/api/prisma/schema.prisma


FROM node:22-slim AS runtime

# openssl: Prisma's query engine links against it and the slim image omits it.
# Without it the client fails at startup with a missing-library error that
# names the file rather than the cause.
RUN apt-get update && apt-get install -y --no-install-recommends openssl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV NODE_ENV=production

# node_modules comes over WHOLE, dev dependencies included, because the deploy
# runs `prisma migrate deploy` from this image and the Prisma CLI is a dev
# dependency. Pruning would save a couple of hundred megabytes and cost the
# ability to migrate — a bad trade for a private image.
COPY --from=build /app/node_modules node_modules
COPY --from=build /app/package.json package.json
COPY --from=build /app/packages/shared packages/shared
COPY --from=build /app/apps/api apps/api

# Never run as root: a container escape should not land on a root shell.
USER node

EXPOSE 4000
CMD ["node", "apps/api/dist/index.js"]
