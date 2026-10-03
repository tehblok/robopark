FROM node:24-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS build

WORKDIR /app

COPY package.json package-lock.json ./
COPY scripts/install-dependencies.sh ./scripts/install-dependencies.sh
RUN --mount=type=cache,id=robopark-web-npm,target=/root/.npm,sharing=locked sh scripts/install-dependencies.sh

COPY . .
RUN npm run build

FROM nginx:1.30.5-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94

RUN apk upgrade --no-cache

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/15-robopark-trusted-proxy.sh /docker-entrypoint.d/15-robopark-trusted-proxy.sh
RUN chmod +x /docker-entrypoint.d/15-robopark-trusted-proxy.sh
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
