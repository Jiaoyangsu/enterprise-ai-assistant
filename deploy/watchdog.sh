#!/bin/bash
# 非 systemd 环境的轻量守护：轮询 /healthz，连续失败后重启连接器。
# 生产优先用 systemd（Restart=always，见 deploy/kbai-connector.service）；
# 本脚本仅用于没有 systemd 的主机兜底：
#   nohup ./deploy/watchdog.sh > /tmp/watchdog.log 2>&1 &
# 环境变量：WATCH_PROBE/INTERVAL/FAILS/WATCH_LOG/PORT
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROBE="${WATCH_PROBE:-http://127.0.0.1:8000/healthz}"
INTERVAL="${WATCH_INTERVAL:-15}"
FAILS="${WATCH_FAILS:-3}"
PORT="${WATCH_PORT:-8000}"
LOG="${WATCH_LOG:-/tmp/watchdog.log}"

log() { printf '[watchdog %s] %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

log "启动，探针=$PROBE 间隔=${INTERVAL}s 阈值=${FAILS} 次"
n=0
while true; do
  sleep "$INTERVAL"
  if curl -fsS "$PROBE" -o /dev/null 2>/dev/null; then
    n=0
    continue
  fi
  n=$((n + 1))
  log "探针失败 $n/$FAILS"
  if [ "$n" -ge "$FAILS" ]; then
    pids="$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
    [ -n "$pids" ] && kill $pids 2>/dev/null && sleep 2
    nohup "$ROOT/start_connector.sh" >>"$LOG" 2>&1 &
    log "已重启连接器（在端口 $PORT 监听）"
    n=0
  fi
done