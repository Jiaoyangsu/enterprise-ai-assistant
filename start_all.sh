#!/bin/bash
# 启动全部 3 个 MCP Server（优先用 venv）
set -e
cd "$(dirname "$0")"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY=python3

echo "Starting docs server (8001)..."
$PY mcp_servers/docs_server.py &
sleep 2
echo "Starting ops server (8002)..."
$PY mcp_servers/ops_server.py &
sleep 2
echo "Starting security server (8003)..."
$PY mcp_servers/security_server.py &

echo "All MCP servers started. Press Ctrl+C to stop all."
wait
