FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# Skip postinstall (prisma generate): the schema isn't copied yet. The builder
# stage generates the client once the full source is present.
RUN npm ci --ignore-scripts

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* values are inlined into the client bundle at build time, so the
# backend URL is a build arg, not a runtime env var.
ARG NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
ENV NEXT_PUBLIC_BACKEND_URL=$NEXT_PUBLIC_BACKEND_URL \
    NEXT_TELEMETRY_DISABLED=1

# Placeholders so modules that read these at import time don't throw during
# `next build`. Real values come from the runtime env file; nothing connects here.
RUN DATABASE_URL="postgresql://build:build@localhost:5432/build" JWT_SECRET="build-placeholder" \
    sh -c "npx prisma generate && npm run build"

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

RUN addgroup -S nodejs && adduser -S nextjs -G nodejs
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs

EXPOSE 3000
ENV PORT=3000 HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
