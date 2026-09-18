#!/bin/bash
# 启动对外聚合连接器（8000）：MCP 原生 OAuth + 可选 TLS + 公开 /healthz /privacy
# 生产建议：由反向代理终止 TLS（见 docs/部署与上线.md），本脚本起 HTTP 监听内网。
set -e
cd "$(dirname "$0")"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY=python3

: "${CONNECTOR_PUBLIC_URL:?请设置 CONNECTOR_PUBLIC_URL=https://<公网域名>（MCP 原生 OAuth 必需）}"

export CONNECTOR_HOST="${CONNECTOR_HOST:-0.0.0.0}"
export CONNECTOR_PORT="${CONNECTOR_PORT:-8000}"
export CONNECTOR_OAUTH_STATE="${CONNECTOR_OAUTH_STATE:-data/oauth_state.json}"
export CONNECTOR_PRIVACY_CONTACT="${CONNECTOR_PRIVACY_CONTACT:-support@example.com}"
export AUDIT_FILE="${AUDIT_FILE:-/var/log/kbai/audit.jsonl}"

if [[ -z "$CONNECTOR_TLS_CERT" || -z "$CONNECTOR_TLS_KEY" ]]; then
  echo "提示：未启用应用层 HTTPS，请在反向代理终止 TLS，或在环境变量中提供 CONNECTOR_TLS_CERT/KEY。"
fi

echo "启动连接器：http(s)://${CONNECTOR_HOST}:${CONNECTOR_PORT} | 公网 ${CONNECTOR_PUBLIC_URL}"
echo "OAuth 状态文件：${CONNECTOR_OAUTH_STATE}（多实例部署须置于共享存储）"
echo "审计日志：${AUDIT_FILE}（受管目录，0600，按容量轮转）"
exec "$PY" mcp_servers/aggregate_server.py
