"""聚合 MCP Server：把 docs/ops/security 合并为单一端点（供 WorkBuddy 连接器上架）。

WorkBuddy 规范要求「一个连接器只配置一个 MCP Server」，因此对外只暴露本聚合
Server（默认端口 8000），内部用 `mount` 挂载三个子 Server，工具名保持不变：

  - docs     (8001) 知识库检索 search_knowledge_base
  - ops      (8002) 员工/预算/部门/合同等业务查询
  - security (8003) PII 脱敏 / 敏感词审查 / 入库清洗

共 12 个工具。连接器侧通过 mcp.json 的 disabledTools 隐藏写工具与依赖
「模型传入角色」的工具（详见 connector/README.md）。
"""
from __future__ import annotations

from fastmcp import FastMCP

import docs_server
import ops_server
import security_server

mcp = FastMCP("enterprise-knowledge-assistant")

for _sub in (docs_server.mcp, ops_server.mcp, security_server.mcp):
    mcp.mount(_sub)


if __name__ == "__main__":
    from connector import run

    run(mcp, port=8000, oauth=True)
