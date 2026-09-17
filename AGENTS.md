# AGENTS.md — 企业知识库助手开发约定

## 架构约定（双实现单一逻辑源）

本系统存在两条实现，必须保持高度一致：

| 侧 | 位置 | 职责 |
|---|---|---|
| **dsh 侧（生产入口）** | `~/.dsh/profiles/web/`（cordis.patch.yml + node_modules/guard/index.js） | 界面、会话、模型路由、护栏插件 |
| **自研侧（MVP 原型）** | `agent/`（react_agent/verifier/web_app.py）+ `mcp_servers/` | 早期验证用，生产不部署 |

- **规则 1：所有业务逻辑改动必须双侧同步**。dsh 侧护栏实现 = `~/.dsh/profiles/web/node_modules/guard/index.js`（JS，对应自研 `agent/verifier.py`）；自研侧任何 verifier/注入/语义规则改动，必须同步移植到 guard/index.js，反之亦然。
- **规则 2：以 dsh 侧共享的 MCP server 为单一数据源**（`mcp_servers/` → 端口 8001/8002/8003/8004），两侧都通过它取数。改 `mcp_servers/*_server.py` 即双侧生效，无需双份。
- **规则 3：生产二选一，默认用 dsh web（8787）作为体验/验收主界面**。自研 8788 仅作等价对照，不作为交付目标。
- **规则 4：开发验证以 dsh 为主**；guard 的 JS 单测在 `~/.dsh/profiles/web/node_modules/guard/test.js`（`node test.js`），与自研 `tests/test_verifier.py` 语义对齐，任何同步改动须两套测试都过。

## 常用命令

- 重启 dsh web：`pkill -f "dsh --profile web"` 后 `nohup dsh --profile web --no-open --port 8787 > /tmp/dsh-web.log 2>&1 &`
- **跨机访问 dsh 8787**：dsh 明确禁止 `--host 0.0.0.0`（会暴露 RCE），只能本机绑定；他机测试用 SSH 隧道 `ssh -L 8787:127.0.0.1:8787 <user>@<Mac IP>` 再开 token 链接（token 取自 `/tmp/dsh-web.log`，每次重启变化）。跨机直连/反代场景需把权威名加入 `--trusted-host`，否则 `/api` 被 browser-trust fence 拦。
- **自研 8788 跨机**：`WEB_HOST=0.0.0.0 .venv/bin/python agent/web_app.py 8788`（默认仍绑 `127.0.0.1`，登录 fail-closed）。
- guard 语法/单测：`node --check ~/.dsh/profiles/web/node_modules/guard/index.js && node ~/.dsh/profiles/web/node_modules/guard/test.js`
- 自研回归：`.venv/bin/python tests/test_verifier.py`（27）、`tests/run_suite.py`（45）、`tests/test_baseline.py`（22）、`tests/test_golden.py`（16）、`tests/test_context.py`（13）、`tests/test_entities.py`（18）、`tests/test_store.py`（20）、`tests/test_retrieval.py`（16）、`tests/test_web_page.py`（内嵌 JS 语法 3）
- **页面内嵌 JS 陷阱**：`agent/web_app.py` 的三引号页面字符串里写 JS 的 `\n` 会被 Python 解释成真实换行、截断 JS 字符串 → 整段脚本 SyntaxError、按钮点了没反应。`tests/test_web_page.py` 用 `node --check` 兜这个；写页面 JS 时换行用空格或改 `\\n`。
- 检索索引：`ollama pull bge-m3`（一次性；离线可跳过，检索自动回退关键词）
- 账号口令管理：`.venv/bin/python tools/set_password.py <姓名> '<口令>'`（或 `--list` / `--remove`）；账号表 `data/auth_users.json`（fail-closed，无通配/默认口令），生产用 `AUTH_USERS` 环境变量注入
- Connector 生产化（`mcp_servers/connector.py`）：`CONNECTOR_CLIENT_SECRET` 启用 Bearer 鉴权，`CONNECTOR_TLS_CERT`/`CONNECTOR_TLS_KEY` 启用 HTTPS；未设置 secret 时为本地 dev（127.0.0.1 不鉴权）
- 日志：`/tmp/dsh-web.log`、`/tmp/guard_feedback.jsonl`（guard 拦截记录）
- 数据飞轮升级为常驻守护进程（自动采集→候选→标注发现→回灌评测→落库知识库→回归）：
  `nohup .venv/bin/python -u tools/flywheel/daemon.py --judge llm > /tmp/flywheel_daemon.log 2>&1 &`
  人工标注只需编辑 `tools/flywheel/candidates.md` 的 `expected` 列（留空=跳过），保存后守护进程自动 promote 到 `benchmark.jsonl`、落库 `data/documents.json` 并跑回归。

## 数据飞轮（架构落点）

- **采集在 dsh 侧**：guard 插件自动写 `/tmp/guard_feedback.jsonl`（guarded/unanswered/needs_human），web_app 写 `/tmp/web_feedback.jsonl`——这是唯一数据入口，不改。
- **汇聚/回灌是独立守护进程** `tools/flywheel/daemon.py`（脱离 dsh 会话模型的批处理作业）：每 60s ingest→report→自动 promote（幂等）→评测集 hash 变化时跑 bench。
- **benchmark.jsonl 永为全量**：`promote()` 从 cases 表全量导出（新增+历史都保留），不得覆盖式只写当次。守护进程 `.bench_last_hash` 标记避免重复跑 bench。
- 人工标注 = 编辑 candidates.md 表格第 5 列 expected（留空跳过）+ 第 8 列 reason；保存即触发自动回灌，无需跑命令。
- **最后一环=新知识自动落库**：`promote()` 末尾调 `ingest_docs()`，把已标注答案写回 `data/documents.json`（ID 从 `DOC-201` 起，`DOC_ID_PREFIX`，避开内置 DOC-001~112；`cases.doc_id` 幂等去重）。`docs_server._documents()` 按文件 mtime 热重载，**落库后无需重启 8001** 即被检索。
- **端到端 bench 有模型随机性**：react_agent 每次由模型生成检索 query（temperature=0.1），偶发 query 表述不命中导致召回波动。判定飞轮是否生效应看「同一 question 落库后检索能命中 + 多次采样可答对」，而非单次 bench。

## 实体记忆与指代消解（`memory_server.py`，8004）

- **单一实现 `mcp_servers/entity_store.py`**：把 `data.py` 的员工/部门/客户/合同建成实体索引（只索引名称/别名/编号/职位/行业等非敏感字段，**不索引手机号/身份证**）。`memory_server.py` 只做薄封装，`react_agent` 也直接 import 它——改逻辑只改 entity_store。
- **长期记忆落点 `data/entities.json`**（`ENTITIES_FILE` 可覆盖）：只存"学到的别名/新实体"，内置实体不落盘（避免与 `data_generated.py` 双份漂移）；文件缺失即纯内置索引。写入用 `remember_entity`（写工具，连接器不对外），删除用 `forget_entity`。
- **指代消解规则**：先用别名精确匹配；命中代词（他/她/那家客户/这个部门/那份合同…）时，取**上下文最后出现的同类型实体**为焦点。代词表在 `entity_store.PRONOUNS`，`find_pronouns` 对 ASCII 加词边界、对"他/她"排除"其他/他们"。
- **RBAC**：客户/合同实体仅 `policy.customer_visible_roles` 可见，员工/部门实体需已登录；与 `ops_server` 同读 `data/policy.json`。Token 连接器场景无会话角色 → 客户/合同实体自动不可见。
- **自研侧**：`react_agent.entity_context_block()` 把"本会话已识别实体 + 指代映射"确定性注入 SYSTEM（不额外调模型）；`resolve_entity` 作为工具时，`user_role`/`is_authenticated`/`context_text` 一律由服务端会话注入（`ctx["_coref_context"]`），模型只给 mention。
- **dsh 侧**：`~/.dsh/.agent-presets/enterprise/agent.cordis.yml` 与 `~/.dsh/profiles/web/cordis.patch.yml` 已挂 `mcp-memory`（8004）；改完需重启 dsh 才注册工具。仓库副本 `profiles/` 同步维护。
- **回归**：`tests/test_entities.py`（18 条：抽取/长词优先/RBAC/指代焦点/代词误命中/记忆写入删除）。

## 检索与持久化（上架第 5、6 条）

- **混合检索**：`mcp_servers/semantic_index.py` 用 ollama `bge-m3`（OpenAI 兼容 `/v1/embeddings`，默认 `http://127.0.0.1:11434`）把文档按 ~420 字分块（带一句重叠）向量化，缓存 `data/embeddings_cache.json`；`docs_server.search_knowledge_base` 用 **RRF 融合**关键词与向量两路召回。改检索只动 `semantic_index.py` 与 `docs_server._fuse_ranks/_best_content/_score_doc`。
- **RBAC 在召回前**：先把无权文档剔除，机密文档**不进入语义索引**（避免侧信道）；embedding 后端不可用时 `semantic_search` 返回空，自动回退纯关键词（行为与旧版一致）。可用 `SEMANTIC_ENABLED=0` 硬关。
- **持久化单一写入口**：`mcp_servers/store.py` → SQLite `data/app.db`（`APP_DB_FILE` 可覆盖，测试用临时库隔离）。首启从 `data_generated.py` 幂等播种；`ops_server` 读写全部经 store，不再直接用 `data_generated` 的 dict。
- **写操作必须事务化**：请假扣余额用 `BEGIN IMMEDIATE` + 条件 UPDATE（并发不超扣）；工单号由 `AUTOINCREMENT` 原子分配（`TK-<id+1000>`）。新增写操作一律在 `store.py` 内实现事务，勿在 server 层改内存。
- **测试**：`tests/test_store.py`（播种/读写/并发不超扣/持久化 20 条）、`tests/test_retrieval.py`（分块/余弦/RRF/RBAC 前置/语义召回 13 条，embedding 不可用时语义断言自动跳过）。
- **数据文件**：`data/app.db`、`data/embeddings_cache.json` 均运行时生成，已 gitignore。

## 上架安全基线（WorkBuddy 连接器）

- **认证 fail-closed**：`agent/secure_auth.py` 集中口令校验（PBKDF2-HMAC-SHA256）；账号表无 `*` 通配、无隐式默认口令，未登记账号一律拒绝。生产用 `AUTH_USERS` 环境变量注入，口令/证书永不入库（`.gitignore` 覆盖 `data/auth_users.json`、`.env`、`*.pem`、`*.key`）。
- **连接器生产化**：`mcp_servers/connector.py` 统一启动，`CONNECTOR_CLIENT_SECRET` → Bearer 鉴权，`CONNECTOR_TLS_CERT/KEY` → HTTPS；满足平台「HTTPS + streamableHttp + client_secret + 单次<30s」。
- **PII 脱敏**：`security_server.redact_pii` 覆盖手机/身份证/邮箱/银行卡/地址/内部人名，敏感词表与内部人名表均由 `data/config.json` 配置。
- **日志脱敏**：`agent/audit.py` 的 `args`/`result` 写入前复用 `security_server.redact_pii`（`_scrub`，失败降级为仅截断）；防止用户把待脱敏 PII 作为参数传入时反被明文落盘。
- **合规材料**：`docs/合规与数据说明.md`（数据驻留 / 日志与审计 / 隐私政策占位 / 凭证处理）、`docs/上架类目与资质.md`（类目选择 + 资质与提交材料清单）。改动数据/日志/鉴权行为须同步这两份文档。
- **LICENSE**：Apache-2.0（无协议不能上架）。

## WorkBuddy 连接器提交包（`connector/`）

- **一个连接器只能绑一个 MCP Server**（平台硬约束）。因此新增 `mcp_servers/aggregate_server.py`：用 `FastMCP.mount`（`namespace=None` 保留原名）把 docs/ops/security 合并为单端点 **8000**，对外 12 个工具。改子 server 即聚合端点同步生效。
- 提交包结构：`connector/{connector-meta.json, mcp.json, token-schema.json, icon.svg, skills/<name>/SKILL.md, README.md}`。
- **`minWorkbuddyVersion` 取所用特性最高版本**：`disabledTools`=4.22.15、`auth_mode: token`+token-schema=4.23.0、`name_zh/en`+`examples_zh/en`=**4.24.0** → 声明 `"4.24.0"`（易错点：不要只写 4.23.0）。
- **对外只读**：`mcp.json` 的 `disabledTools` 隐藏 `create_leave_request`/`create_ticket`（写操作未持久化）与 `get_customer_info`/`list_customers`（权限依赖模型传入的 `user_role`，Token 模式不可信）。
- **鉴权**：`auth_mode: token`，`${MCP_TOKEN}`（mcp.json）↔ `token-schema.json` 的 `fields[].key` 大小写必须一致；服务端 `CONNECTOR_CLIENT_SECRET` 对应此令牌。
- 本地联调：`cd mcp_servers && CONNECTOR_CLIENT_SECRET=... ../.venv/bin/python aggregate_server.py`；校验 `curl -o /dev/null -w '%{http_code}' 127.0.0.1:8000/mcp`（无 token 应 401）。
- 提交阻塞项见 `connector/README.md`：公网 HTTPS 域名、`SKILL.md` 的 `category` 白名单核对、令牌签发流程。