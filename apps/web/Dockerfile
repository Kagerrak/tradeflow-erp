FROM node:22.18.0-bookworm-slim AS build
ENV PNPM_HOME="/pnpm" PATH="/pnpm:$PATH"
RUN corepack enable && corepack prepare pnpm@9.7.0 --activate
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages packages
RUN pnpm install --frozen-lockfile
COPY apps/web apps/web
RUN pnpm --filter @tradeflow/web build

FROM node:22.18.0-bookworm-slim AS runtime
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
RUN groupadd --system --gid 10001 tradeflow && useradd --system --uid 10001 --gid tradeflow tradeflow
COPY --from=build --chown=tradeflow:tradeflow /app/apps/web/.next/standalone ./
COPY --from=build --chown=tradeflow:tradeflow /app/apps/web/.next/static ./apps/web/.next/static
USER tradeflow
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
