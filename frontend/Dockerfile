# hitsai/midataworks-frontend (ADR-024; Foundation task 15.1). Built in CI on the mirror only,
# never locally. Adapted from miStudio's frontend/Dockerfile: a Node build stage, then
# nginx-unprivileged (uid 101, port 8080) serving the static build. The dev-server stage is
# dropped: local development runs Vite from source (docker-compose.yml runs only the databases).

# Node 20 matches frontend-ci.yml, so the image builds with the toolchain CI tested.
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:alpine AS production
COPY --chown=nginx:nginx nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder --chown=nginx:nginx /app/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/health || exit 1
CMD ["nginx", "-g", "daemon off;"]
