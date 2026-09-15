# 企业知识库 AI 助手

基于 **dsh 框架 + 自研 Agent（ReAct）+ MCP Servers** 的企业 AI 助手，覆盖员工 / 预算 / 客户 / 合规 / 内部流程场景。

> 架构思路：90% 用 dsh 框架打底（会话、路由、工具编排、降级），10% 做业务定制化（登录/RBAC、流程工作流、人工兜底坐席、数据飞轮评测）。框架负责通用能力，定制层才是本助手的差异化。

## 架构

```
dsh (框架) ── MCP Servers (业务数据) ── agent/react_agent.py (ReAct 循环)
                                            ├── web_app.py     (自建前端: 登录/RBAC/坐席页)
                                            ├── workflow.py    (9 类流程: 申请引导/草稿/审批查询)
                                            ├── verifier.py    (回答回检器, 压幻觉)
                                            └── llm.py         (LLM 后端: 本地 ollama 默认)
tools/flywheel.py ── 数据飞轮(采集→分类→候选→promote→bench 评测)
```

| 组件 | 端口 | 文件 |
|------|------|------|
| Docs Server（知识库检索） | 8001 | `mcp_servers/docs_server.py` |
| Ops Server（员工/预算/客户/合同） | 8002 | `mcp_servers/ops_server.py` |
| Security Server（脱敏/风险检查） | 8003 | `mcp_servers/security_server.py` |
| dsh Web（框架前端） | 8787 | dsh 框架 |
| 自建前端（登录/RBAC/坐席） | 8788 | `agent/web_app.py` |
| 本地 LLM（ollama qwen2.5:14b） | 11434 | 切换脚本 `tools/switch_backend.sh` |

## 快速启动

```bash
# 1. 启动 3 个业务 MCP Server（依赖 fastmcp，见 .venv）
./start_all.sh

# 2. 启动自建前端（登录 + RBAC + 人工兜底坐席）
.venv/bin/python agent/web_app.py 8788
#     打开 http://127.0.0.1:8788 → 姓名+密码登录（默认密码 123456，见 auth_users.json）

# 3. 自检
./check_servers.sh          # MCP 连通性
.venv/bin/python tools/llm_health.py   # LLM 后端健康
```

## 能力总览

- **多轮问答**：ReAct 循环调用真实 MCP 工具取证，绝不编数据；`verifier` 回检器在交付前审查无源断言，有幻觉兜底重答一次。
- **登录与 RBAC**：`web_app.py` 姓名+密码登录（cookie session）；身份注入 Agent → 客户信息按角色授限（经理可见 / 普通员工被拒）。
- **流程工作流（workflow.py，9 类）**：报销 / 请假 / 加班 / 资产申领 / 出差 / 权限申请 / 证明开具 / 培训申请 / 审批查询。
  - 缺字段 → 引导补齐；字段齐 → 代码直接生成可提交草稿清单；审批查询 → 告知到 OA「我的申请」查看，不编造审批节点。
- **人工兜底坐席**：低置信 / 无法确认 / 500 类答案自动进 `/tmp/human_queue.jsonl` → 人工在 `/human` 页回填 → 回灌飞轮。
- **数据飞轮（tools/flywheel.py）**：生产日志采样 → sqlite 去重分类（guarded/unanswered/human/error）→ 候选清单 `candidates.md` → 人工标注 expected → `promote` 录入评测集 → `bench` 跑分。
- **三套边界**：RBAC 权限边界（客户/密级文档）、全读白名单（写工具永不给，杜绝副作用）、边界拒答规则（`agent/boundaries.py`）。

## 配置位置

- 前端登录密码表：`auth_users.json`（`*` 通配默认密码；可用 `AUTH_DEFAULT_PASSWORD` 环境变量覆盖默认）
- Session 有效期：环境变量 `SESSION_HOURS`（默认 12h）
- LLM 后端切换：`tools/switch_backend.sh local|autodl`
- Persona / dsh 配置：`~/.dsh/.agent-presets/enterprise/agent.cordis.yml`（规则 4/10/11 流程引导）
- Guard 拦截规则三分同步：`/opt/homebrew/lib/node_modules/guard/index.js` ↔ `~/.dsh/profiles/web/node_modules/guard/` ↔ `profiles/guard/index.js`

## 业务数据

`mcp_servers/data.py`（在职员工目录、预算、客户、合同、部门）+ `mcp_servers/documents.py` / `documents_extra.py`（27 篇制度/实操文档，含密级 RBAC）。员工目录会随 `generate_data.py` 生成扩展。

## 文档

- [使用指南](docs/使用指南.md) — 面向使用者与运维：登录、提问边界、工作流用法、坐席页、后端切换
- [测试文档](docs/测试文档.md) — 自动化测试 / 冒烟 / 端到端验证 / 飞轮 eval 与 bench 流程
- [变更日志](CHANGELOG.md)