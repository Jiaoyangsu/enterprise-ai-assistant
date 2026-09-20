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
| Memory Server（实体记忆/指代消解） | 8004 | `mcp_servers/memory_server.py` |
| 语义检索索引（本地 embedding） | 11434 | `mcp_servers/semantic_index.py`（ollama `bge-m3`） |
| 业务数据库（SQLite 持久化） | — | `mcp_servers/store.py` → `data/app.db` |
| dsh Web（框架前端） | 8787 | dsh 框架 |
| 自建前端（登录/RBAC/坐席） | 8788 | `agent/web_app.py` |
| 本地 LLM（ollama qwen2.5:14b） | 11434 | 切换脚本 `tools/switch_backend.sh` |

## 快速启动

```bash
# 0. 拉取本地 embedding 模型（混合检索用，一次性；离线环境可跳过，检索自动回退关键词）
ollama pull bge-m3

# 1. 启动 4 个业务 MCP Server（依赖 fastmcp，见 .venv）
./start_all.sh

# 2. 启动自建前端（登录 + RBAC + 人工兜底坐席）
.venv/bin/python agent/web_app.py 8788
#     打开 http://127.0.0.1:8788 → 姓名+密码登录（无默认口令；账号见 data/auth_users.json，用 tools/set_password.py 管理）
#     他人/跨机测试：WEB_HOST=0.0.0.0 .venv/bin/python agent/web_app.py 8788 → http://<本机IP>:8788（仍需登录）

# 3. 自检
./check_servers.sh          # MCP 连通性
.venv/bin/python tools/llm_health.py   # LLM 后端健康
```

## 能力总览

- **多轮问答**：会话历史跨轮带入（支持"孙敏在哪个部门"→"这个部门多少人"的指代续问）；ReAct 循环调用真实 MCP 工具取证，绝不编数据；`verifier` 回检器在交付前审查无源断言，有幻觉兜底重答一次。
- **实体记忆 + 指代消解**：`mcp_servers/entity_store.py` 把员工/部门/客户/合同建成实体索引（客户/合同受 RBAC）；`memory_server.py`（8004）提供 `resolve_entity`/`extract_entities`，代词（他/那家客户/这个部门/那份合同）按"上下文最后出现的同类型实体"定焦点解析成规范名；自研侧还会把"本会话已识别实体 + 指代映射"确定性注入 SYSTEM。学到的别名/新实体落 `data/entities.json`。
- **混合检索（关键词 + 本地语义向量）**：`semantic_index.py` 用 ollama `bge-m3` 对文档分块向量化（缓存 `data/embeddings_cache.json`），RRF 融合关键词与向量两路召回，解决中文同义复述失准；RBAC 在召回前过滤，机密文档不进入语义索引。embedding 不可用时自动回退纯关键词。
- **数据持久化（SQLite）**：`store.py` 把员工/部门/预算/客户/合同 + 请假/工单落 `data/app.db`（首启幂等播种）；请假扣减余额走 `BEGIN IMMEDIATE` 条件更新、工单号由 AUTOINCREMENT 分配，重启不丢、并发不超扣。
- **登录与 RBAC**：`web_app.py` 姓名+密码登录（cookie session）；身份注入 Agent → 客户信息按角色授限（经理可见 / 普通员工被拒）。
- **流程工作流（workflow.py，9 类）**：报销 / 请假 / 加班 / 资产申领 / 出差 / 权限申请 / 证明开具 / 培训申请 / 审批查询。
  - 缺字段 → 引导补齐；字段齐 → 代码直接生成可提交草稿清单；审批查询 → 告知到 OA「我的申请」查看，不编造审批节点。
- **人工兜底坐席**：低置信 / 无法确认 / 500 类答案自动进 `/tmp/human_queue.jsonl` → 人工在 `/human` 页回填 → 回灌飞轮。
- **数据飞轮（tools/flywheel.py）**：生产日志采样 → sqlite 去重分类（guarded/unanswered/human/error）→ 候选清单 `candidates.md` → 人工标注 expected → `promote` 录入评测集 → `bench` 跑分。
- **三套边界**：RBAC 权限边界（客户/密级文档）、全读白名单（写工具永不给，杜绝副作用）、边界拒答规则（`agent/boundaries.py`）。

## 配置位置

- 账号口令：`data/auth_users.json`（PBKDF2 哈希，**fail-closed：仅登记账号可登录**，无通配/默认口令）；用 `.venv/bin/python tools/set_password.py <姓名> '<口令>'` 管理，或生产用 `AUTH_USERS` 环境变量(JSON) 注入（不落盘）
- Session 有效期：环境变量 `SESSION_HOURS`（默认 12h）
- RBAC 权限策略：`data/policy.json`（客户可见角色/文档密级/坐席角色，唯一权限声明点）
- LLM 后端切换：`tools/switch_backend.sh local|autodl`
- 混合检索：`EMBED_MODEL`(默认 `bge-m3`)/`EMBED_BASE_URL`(默认 `http://127.0.0.1:11434`)/`SEMANTIC_ENABLED`(0 关闭)；向量缓存 `data/embeddings_cache.json`
- 业务数据库：`APP_DB_FILE`(默认 `data/app.db`，测试可用临时库隔离)
- Persona / dsh 配置：`~/.dsh/.agent-presets/enterprise/agent.cordis.yml`（规则 4/10/11 流程引导）
- Guard 拦截规则两侧同步（dsh 实际加载 `node_modules` 那份，repo 仅存副本）：`~/.dsh/profiles/web/node_modules/guard/` ↔ `profiles/guard/`（`index.js`/`test.js`/`package.json`）；改完跑 `node profiles/guard/test.js` 并重启 dsh

## 上架 Connector（生产化）

各 MCP Server 已是 streamable-http，满足 WorkBuddy 连接器接入方式；生产化开关见 `mcp_servers/connector.py`：

- `CONNECTOR_PUBLIC_URL`：对外 HTTPS 根地址，设置后聚合端点（8000）启用 **MCP 原生 OAuth 2.1（公共客户端 + PKCE）**，端点/元数据见 `mcp_servers/oauth_server.py`，状态落盘 `data/oauth_state.json`（0600，重启不掉线）。
- `CONNECTOR_CLIENT_SECRET`：OAuth 未启用时的**静态 Bearer 兜底**（仅供内网联调）；都未设置=本地开发（仅监听 `127.0.0.1`，不鉴权）。
- `CONNECTOR_TLS_CERT` / `CONNECTOR_TLS_KEY`：同时设置则启用 HTTPS；`CONNECTOR_PORT` 覆盖监听端口（默认 8000）。
- `CONNECTOR_MAX_CALL_SECONDS`：工具调用**硬上限**（默认 30s，`mcp_servers/tool_timeout.py` 在协议层强制），满足平台「单次 <30s」。
- 同一 HTTPS 源另提供公开端点：`/healthz`（可用性探针）与 `/privacy`（隐私政策页，`CONNECTOR_PRIVACY_FILE` 可用法务终稿覆盖），由 `mcp_servers/public_app.py` 提供。
- 启动：`CONNECTOR_PUBLIC_URL=https://<域名> ./start_connector.sh`；完整部署（反代/证书/systemd/多实例/监控/真机验收）见 [`docs/部署与上线.md`](docs/部署与上线.md)，`deploy/` 提供现成的 systemd 单元与环境变量模板，备份/恢复与数据接入分别见 `docs/备份与恢复.md`、`docs/数据接入.md`。

## 许可

Apache-2.0，见 [LICENSE](LICENSE)。

## 业务数据

`mcp_servers/data.py`（在职员工目录、预算、客户、合同、部门）+ `mcp_servers/documents.py` / `documents_extra.py`（27 篇制度/实操文档，含密级 RBAC）。员工目录会随 `generate_data.py` 生成扩展；默认是合成演示数据，上线换客户真实数据用
`tools/import_customer_data.py`（CSV→SQLite/documents.json，见 [`docs/数据接入.md`](docs/数据接入.md)）。

## 运维工具

- `tools/backup.sh` — `data/` 一致性快照 + 保留轮转（恢复见 [`docs/备份与恢复.md`](docs/备份与恢复.md)）
- `deploy/` — systemd 单元 + `connector.env.example` + 无 systemd 的 `watchdog.sh` 兜底
- `tools/run_all_tests.sh` + `.github/workflows/tests.yml` — 一键全量回归 / CI

## 文档

- [使用指南](docs/使用指南.md) — 面向使用者与运维：登录、提问边界、工作流用法、坐席页、后端切换
- [测试文档](docs/测试文档.md) — 自动化测试 / 冒烟 / 端到端验证 / 飞轮 eval 与 bench 流程
- [数据接入](docs/数据接入.md) — 真实数据替换合成数据（上线第一天）
- [备份与恢复](docs/备份与恢复.md) — 一致性快照、恢复演练、恢复注意事项
- [变更日志](CHANGELOG.md)