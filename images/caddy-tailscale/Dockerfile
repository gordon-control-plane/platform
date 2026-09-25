FROM caddy:2.11-builder-alpine@sha256:2b9f32cbba6045e79212bb253f595588d20be8f7d1edf61582c519e124e0d2d8 AS builder

RUN xcaddy build \
    --with github.com/tailscale/caddy-tailscale

FROM caddy:2.11-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
# hadolint ignore=DL3018
RUN apk add --no-cache libcap && setcap -r /usr/bin/caddy || true

USER 1000:1000
