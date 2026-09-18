#!/bin/bash
# 启动全部 4 个 MCP Server（优先用 venv）
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
sleep 2
echo "Starting memory server (8004)..."
$PY mcp_servers/memory_server.py &
sleep 2
echo "Starting aggregate/connector server (8000)..."
$PY mcp_servers/aggregate_server.py &

echo "All MCP servers started (incl. connector 8000). Press Ctrl+C to stop all."
wait
