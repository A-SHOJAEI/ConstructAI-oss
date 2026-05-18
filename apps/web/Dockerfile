# ---- Stage 1: Dependencies ----
# Pinned patch release for reproducibility. When rotating, update all three
# stages (deps, builder, runner) together.
# TODO(ops): replace tag with @sha256:<digest> pin once the registry mirror
# tracks digests.
FROM node:20.17.0-alpine3.20 AS deps

WORKDIR /app

# Monorepo: package-lock.json lives at the repo root because workspaces are
# managed there. For an apps/web-scoped build context (which docker-compose
# uses by default) we don't have access to it, so install without the lock
# file. For reproducible CI builds, set the build context to the repo root
# and `COPY ../package-lock.json .` instead.
COPY package.json ./
COPY package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install --no-audit --no-fund; fi

# ---- Stage 2: Builder ----
FROM node:20.17.0-alpine3.20 AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# Fail if building for production with localhost URL
ARG NODE_ENV=production
RUN if [ "$NODE_ENV" = "production" ] && echo "$NEXT_PUBLIC_API_URL" | grep -q "localhost"; then \
      echo "FATAL: NEXT_PUBLIC_API_URL contains localhost in production build"; exit 1; \
    fi

RUN npm run build

# ---- Stage 3: Runner ----
FROM node:20.17.0-alpine3.20 AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q --spider http://localhost:3000 || exit 1

CMD ["node", "server.js"]
