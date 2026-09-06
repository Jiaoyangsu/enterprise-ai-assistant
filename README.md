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

## 工具清单（11 个）

- **Ops (8002)**：`lookup_employee`(员工+年假合并) `query_budget` `list_departments` `get_customer_info`(RBAC) `query_contract` `create_leave_request` `create_ticket`
- **Docs (8001)**：`search_knowledge_base`(TF-IDF + RBAC 密级拦截)
- **Security (8003)**：`redact_pii` `risk_review_text` `sanitize_for_storage`

## 业务数据

`mcp_servers/data.py` — 8 部门 / 30 员工 / 年度预算 / 客户 / 合同 / 9 篇知识库文档（含密级）。

## 测试集

- `tests/test_baseline.py` — 21 条逻辑回归基线
- `tests/test_questions.py` — 6 组 × 5 道 = 30 题（复杂度 L1~L5）
- `tests/router.py` — 混合路由规则引擎
- `tests/run_suite.py` — 30 题路由命中 + 实际工具调用验证

运行：`.venv/bin/python tests/run_suite.py`
