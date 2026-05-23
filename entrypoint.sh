#!/bin/sh
# Permite sobrescrever o UID/GID do processo via PUID/PGID
# Ex: environment: [ PUID=1026, PGID=100 ]
PUID=${PUID:-1000}
PGID=${PGID:-1000}

if [ "$(id -u)" = "0" ]; then
    groupmod -o -g "$PGID" pipeline 2>/dev/null || true
    usermod  -o -u "$PUID" pipeline 2>/dev/null || true
    chown pipeline:pipeline /db 2>/dev/null || true
    exec su-exec pipeline "$@"
else
    exec "$@"
fi
