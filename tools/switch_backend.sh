#!/bin/bash
# 一键切换默认后端：本地 14b (11434) <-> AutoDL 32b (11435 隧道)
# 用法: bash tools/switch_backend.sh local|autodl
set -e
SETTINGS="$HOME/.dsh/settings.yaml"
case "$1" in
  local)
    awk '/agent-default-model:/{f=1} f&&/provider:/{sub(/provider: .*/,"provider: user-ollama")} f&&/model:/{sub(/model: .*/,"model: qwen2.5:14b"); f=0} {print}' "$SETTINGS" > "$SETTINGS.tmp" && mv "$SETTINGS.tmp" "$SETTINGS"
    echo "== 已切到本地 14b (11434)，重启 dsh 生效 =="
    ;;
  autodl)
    awk '/agent-default-model:/{f=1} f&&/provider:/{sub(/provider: .*/,"provider: autodl")} f&&/model:/{sub(/model: .*/,"model: qwen2.5:32b"); f=0} {print}' "$SETTINGS" > "$SETTINGS.tmp" && mv "$SETTINGS.tmp" "$SETTINGS"
    echo "== 已切到 AutoDL 32b (11435 隧道)，重启 dsh 生效 =="
    ;;
  *)
    echo "用法: bash tools/switch_backend.sh local|autodl"; exit 1;;
esac
awk '/agent-default-model:/{p=1} p{print} /^$/{if(p)exit}' "$SETTINGS"