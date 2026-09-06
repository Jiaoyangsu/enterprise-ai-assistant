#!/bin/bash
# 启动全部 3 个 MCP Server
set -e
cd "$(dirname "$0")"

echo "Starting docs server (8001)..."
python mcp_servers/docs_server.py &
sleep 1
echo "Starting ops server (8002)..."
python mcp_servers/ops_server.py &
sleep 1
echo "Starting security server (8003)..."
python mcp_servers/security_server.py &

echo "All MCP servers started. Wait for logs to confirm."
wait
