# 企业知识库 AI 助手

基于 dsh + MCP 的企业 AI 助手，覆盖员工/预算/客户/合规场景。

## 架构

```
dsh (框架) + MCP Servers (业务) + Persona (规则约束)
```

| 组件 | 端口 | 文件 |
|------|------|------|
| Docs Server | 8001 | `mcp_servers/docs_server.py` |
| Ops Server | 8002 | `mcp_servers/ops_server.py` |
| Security Server | 8003 | `mcp_servers/security_server.py` |

## 启动

```bash
pip install fastmcp

# 分 3 个终端启动 或 用脚本
./start_all.sh

# 验证
./check_servers.sh
```

## 配置位置

- Persona: `config/.agent-presets/enterprise/persona.md`
- Patch: `profiles/web/cordis.patch.yml`
  - 复制到 `~/.dsh/profiles/web/cordis.patch.yml`
  - Persona 复制到 `~/.dsh/.agent-presets/enterprise/persona`

## 工具清单

详见各 server 内 `@mcp.tool()` 装饰器。

## 基线测试

`tests/test_baseline.py` — 20 条业务查询基线。
