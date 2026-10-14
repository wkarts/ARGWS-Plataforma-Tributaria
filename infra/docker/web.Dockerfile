ARG NODE_BASE=ghcr.io/wkarts/reforma-tributaria-base-node:node22-r1
ARG NGINX_BASE=ghcr.io/wkarts/reforma-tributaria-base-nginx:nginx1.30-r1
FROM --platform=$BUILDPLATFORM ${NODE_BASE} AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

FROM ${NGINX_BASE}
ARG APP_VERSION
ARG BUILD_SHA
LABEL org.opencontainers.image.source="https://github.com/wkarts/ARGWS-Plataforma-Tributaria" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${BUILD_SHA}"
COPY infra/docker/nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=nginx:nginx /app/dist/ /usr/share/nginx/html/
USER nginx
EXPOSE 8080
