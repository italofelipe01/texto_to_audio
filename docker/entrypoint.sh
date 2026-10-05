#!/bin/sh
# Prepara o volume /data e executa o comando sem privilégios de root.
set -e

mkdir -p /data/entrada /data/saida 2>/dev/null || true

if [ "$(id -u)" = "0" ]; then
    # Volumes montados do host costumam pertencer a outro usuário: ajusta uma única vez.
    if [ "$(stat -c %u /data)" != "1000" ]; then
        chown -R 1000:1000 /data 2>/dev/null || echo "aviso: não foi possível ajustar as permissões de /data"
    fi
    export HOME=/home/app
    # drop privileges without extra packages (gosu/setpriv)
    exec python -c 'import os, sys; os.setgroups([]); os.setgid(1000); os.setuid(1000); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
fi

exec "$@"
