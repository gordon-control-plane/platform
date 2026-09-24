FROM caddy:2.7.6-builder-alpine AS builder

RUN xcaddy build \
    --with github.com/tailscale/caddy-tailscale

FROM caddy:2.7.6-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
RUN apk add --no-cache libcap && setcap -r /usr/bin/caddy || true

USER 1000:1000
