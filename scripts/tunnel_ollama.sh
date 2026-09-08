#!/bin/bash
# 本机 -> AutoDL Ollama SSH 隧道
# 用法: ~/bin/tunnel_ollama.sh
set -e
PORT_ALIAS="${DSH_OLLAMA_SSH_PORT:-41036}"
HOST="${DSH_OLLAMA_SSH_HOST:-connect.cqa1.seetacloud.com}"
if ! pgrep -f "ssh.*-L 11434:127.0.0.1:11434" >/dev/null 2>&1; then
  ssh -f -N -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=60 -o ServerAliveCountMax=3 \
    -p "$PORT_ALIAS" "root@$HOST" -L 11434:127.0.0.1:11434
  echo "tunnel up: local 11434 -> $HOST"
else
  echo "tunnel already running"
fi
curl -s --max-time 5 http://127.0.0.1:11434/api/version && echo " (ollama reachable)"