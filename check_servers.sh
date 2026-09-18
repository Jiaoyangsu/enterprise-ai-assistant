#!/bin/bash
# 连通性自检：4 个内部 MCP server + 1 个对外聚合端点（8000，连接器唯一入口）
# 8000 鉴权开启时无 token 返回 401，也算「可达」。
check() {
  local port=$1 name=$2
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port/mcp" 2>/dev/null)
  if [[ "$code" =~ ^(200|400|401|405)$ ]]; then
    echo "port $port ($name): OK ($code)"
  else
    echo "port $port ($name): FAIL (not reachable, code=$code)"
  fi
}

check 8001 docs
check 8002 ops
check 8003 security
check 8004 memory
check 8000 connector
