FROM caddy:2.8-builder-alpine@sha256:4ac56838081008c467ae701ebf425c53db62f91e5fa723151904b9ee75d09346 AS builder

RUN xcaddy build \
    --with github.com/tailscale/caddy-tailscale

FROM caddy:2.8-alpine@sha256:af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
RUN apk add --no-cache libcap && setcap -r /usr/bin/caddy || true

USER 1000:1000
