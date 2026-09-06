#!/bin/bash
# 简单连通性自检：测试 3 个 server 是否可访问
for port in 8001 8002 8003; do
  if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$port/mcp 2>/dev/null | grep -qE "200|400|405"; then
    echo "port $port: OK"
  else
    echo "port $port: FAIL (not reachable)"
  fi
done
