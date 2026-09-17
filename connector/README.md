# WorkBuddy 连接器提交包 · 企业知识助手

把一个企业知识库 + 业务查询 MCP Server 包装成 WorkBuddy **Connector（MCP + Skill）** 提交审核。

## 目录结构

```
connector/
├── connector-meta.json   # 注册与市场展示（双语）
├── mcp.json              # MCP 连接配置（streamableHttp + Bearer + disabledTools）
├── token-schema.json     # 用户自填 Token 表单（auth_mode: token）
├── icon.svg              # 市场图标（64×64，透明背景）
├── README.md             # 本文件
└── skills/
    └── enterprise-knowledge-assistant/
        └── SKILL.md      # 指导 AI 正确调用工具
```

## 服务端（本仓库）

对外只需一个 MCP 端点（规范：**一个连接器只配置一个 MCP Server**）。本仓库用
`mcp_servers/aggregate_server.py` 把三个子 Server 合并到单端点：

```
docs(8001) + ops(8002) + security(8003)  -->  aggregate_server.py (8000)  -->  /mcp
```

启动（生产需 HTTPS + 鉴权）：

```bash
cd mcp_servers
export CONNECTOR_CLIENT_SECRET='<强随机令牌>'
export CONNECTOR_TLS_CERT=/path/fullchain.pem
export CONNECTOR_TLS_KEY=/path/privkey.pem
../.venv/bin/python aggregate_server.py
```

- 未设置 `CONNECTOR_CLIENT_SECRET` = 本地开发模式（仅监听 127.0.0.1，不鉴权）。
- 设置了 secret 后，所有请求必须携带 `Authorization: Bearer <secret>`（常量时间比较）。
- 传输为 `streamable-http`，符合平台「HTTPS + SSE/streamableHttp」要求。

## 鉴权（auth_mode: token）

用户在安装时填写访问令牌，仅存本机 `~/.workbuddy`，连接时以 `Bearer ${MCP_TOKEN}`
注入请求头。占位符名 `MCP_TOKEN` 与 `token-schema.json` 的 `fields[].key` **完全一致**。
令牌对应服务端的 `CONNECTOR_CLIENT_SECRET`，由企业管理员签发/轮换。

## 工具清单（12 个中对外暴露 8 个只读）

`mcp.json` 的 `disabledTools` 隐藏 4 个：

| 被隐藏工具 | 原因 |
| --- | --- |
| `create_leave_request` | 写操作；当前写操作尚未持久化（重启即丢），不适合对外 |
| `create_ticket` | 写操作；同上，且平台要求高风险确认 + 幂等键，v1 先不开放 |
| `get_customer_info` | 权限依赖**模型传入的 `user_role` 参数**，Token 模式无用户会话，无法可信判定（官方明确要求不得依赖模型传入的用户身份） |
| `list_customers` | 同上 |

对外暴露（只读）：`search_knowledge_base`、`lookup_employee`、`query_budget`、
`list_departments`、`query_contract`、`redact_pii`、`risk_review_text`、`sanitize_for_storage`。

> 客户信息类工具若要开放，需把「角色」从模型参数改为**服务端按令牌映射**（P1 待办）。

## 版本声明

`minWorkbuddyVersion` 取所用新特性的**最高版本**：

| 使用到的特性 | 最低版本 |
| --- | --- |
| `mcp.json` 的 `disabledTools` | 4.22.15 |
| `auth_mode: token` + `token-schema.json` | 4.23.0 |
| `name_zh/name_en`、`examples_zh/examples_en` | 4.24.0 |

→ 因此 `connector-meta.json` 声明 **`"minWorkbuddyVersion": "4.24.0"`**。

## 提交前检查（对照官方清单）

- [x] 目录结构符合规范（meta + mcp + icon + skills）
- [x] `source` 为 kebab-case 且全局唯一（`enterprise-knowledge-assistant`）
- [x] 名称 / 说明 / 中英文示例完整（示例各 4 条）
- [x] 仅配置一个 MCP Server，远程地址用 HTTPS
- [x] `auth_mode: token`，`${MCP_TOKEN}` 与表单 key 一一对应，敏感字段为 `password`
- [x] 未在任何文件中硬编码真实凭证（全部用占位符）
- [x] 图标 64×64、透明背景、小尺寸可辨
- [x] 版本号与 `minWorkbuddyVersion` 正确
- [x] Skill 覆盖工具用途、参数、示例、认证前置与错误恢复
- [x] 已隐藏写工具与不可信授权工具（最小权限）

## 提交前仍需完成（阻塞项）

1. **公网 HTTPS 端点**：把 `aggregate_server.py` 部署到可公网访问的 HTTPS 地址，
   然后替换 `mcp.json` 的 `url` 与 `token-schema.json` 的 `docUrl` 中的 `YOUR_DOMAIN`。
   平台建议可用性 ≥ 99.9%。
2. **`category` 白名单**：`SKILL.md` 中 `category: productivity` 需与官方分类白名单核对
   （以后台/最新文档为准）。
3. **令牌签发流程**：明确企业管理员如何生成/轮换 `CONNECTOR_CLIENT_SECRET`（`docUrl` 页面）。

## 合规材料（随包提交审核）

- [`docs/合规与数据说明.md`](../docs/合规与数据说明.md)：数据驻留（默认本地推理、不出网）、日志脱敏、隐私政策占位、凭证处理。
- [`docs/上架类目与资质.md`](../docs/上架类目与资质.md)：类目选择、主体资质与提交材料清单。

## 打包

```bash
cd connector && zip -r ../enterprise-knowledge-assistant.zip . -x '.*'
```
