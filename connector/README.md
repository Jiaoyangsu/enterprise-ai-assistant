# WorkBuddy 连接器提交包 · 企业知识助手

把一个企业知识库 + 业务查询 MCP Server 包装成 WorkBuddy **Connector（MCP + Skill）** 提交审核。

## 目录结构

```
connector/
├── connector-meta.json   # 注册与市场展示（双语，auth_mode 省略 = MCP 原生 OAuth）
├── mcp.json              # MCP 连接配置（streamableHttp + disabledTools，无凭证头）
├── icon.svg              # 市场图标（规范推荐 SVG，64×64 viewBox，透明背景）
├── icon.png              # 512×512 透明 PNG（头像/位图备用）
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

启动（生产需公网 HTTPS）：

```bash
cd mcp_servers
export CONNECTOR_PUBLIC_URL='https://kb.example.com'   # 对外 HTTPS 根地址 → 启用 MCP 原生 OAuth
export CONNECTOR_TLS_CERT=/path/fullchain.pem          # 若用反代终止 TLS 可省略
export CONNECTOR_TLS_KEY=/path/privkey.pem
../.venv/bin/python aggregate_server.py
```

- 设置 `CONNECTOR_PUBLIC_URL` = **MCP 原生 OAuth** 模式（推荐，`auth_mode` 省略）。
- 未设置 `CONNECTOR_PUBLIC_URL` 但设置了 `CONNECTOR_CLIENT_SECRET` = 静态 Bearer（仅供内网联调）。
- 两者都未设置 = 本地开发模式（仅监听 `127.0.0.1`，不鉴权）。
- 传输为 `streamable-http`，符合平台「HTTPS + SSE/streamableHttp」要求。

## 鉴权（MCP 原生 OAuth 2.1，公共客户端 + PKCE）

`connector-meta.json` **省略 `auth_mode`**，由 WorkBuddy 内置 OAuth 管理器按标准 MCP 流程接入
（公共客户端，不持有 `client_secret`）。服务端由 `mcp_servers/oauth_server.py` 提供：

| 端点 | 说明 |
| --- | --- |
| `/.well-known/oauth-protected-resource`（及 `/mcp` 后缀） | 资源元数据，返回 `resource` 与 `authorization_servers` |
| `/.well-known/oauth-authorization-server` | 授权服务器元数据（端点均在 `/oauth/*`，含 `S256` 与公共客户端 `none`） |
| `POST /oauth/register` | 动态注册，接受公共客户端并**回显 `redirect_uris`** |
| `GET /oauth/authorize` | PKCE（`code_challenge_method=S256`） |
| `POST /oauth/token` | 校验 `code_verifier`，签发 access/refresh token，支持 refresh grant |

- redirect_uri **精确匹配**：接受 `workbuddy://workbuddy/mcp/connector%3Aenterprise-knowledge-assistant/oauth/callback`
  与回退 `http://127.0.0.1:{动态端口}/oauth/callback`（`oauth_server._redirect_allowed` 白名单）。
- access_token ≈1h、refresh_token ≥30d、授权码一次性 ≈10min，与官方建议一致。
- 未带 Token 的请求返回 `401` 且附 `WWW-Authenticate: Bearer resource_metadata=...`（RFC 9728 发现入口）。
- 动态注册的客户端与令牌持久化到 `data/oauth_state.json`（0600，已 gitignore），进程重启不掉线。

> 若目标客户只支持自填 Token，请按官方要求**另起一个 `source`** 提交独立的 token 连接器
> （同一服务不得在同一连接器中同时提供 OAuth 与 Token）。

## 工具清单（12 个中对外暴露 8 个只读）

`mcp.json` 的 `disabledTools` 隐藏 4 个：

| 被隐藏工具 | 原因 |
| --- | --- |
| `create_leave_request` | 写操作；对外需高风险确认 + 幂等键，v1 先不开放 |
| `create_ticket` | 写操作；同上 |
| `get_customer_info` | 权限依赖**模型传入的 `user_role` 参数**，不可信（官方明确要求不得依赖模型传入的用户身份） |
| `list_customers` | 同上 |

对外暴露（只读）：`search_knowledge_base`、`lookup_employee`、`query_budget`、
`list_departments`、`query_contract`、`redact_pii`、`risk_review_text`、`sanitize_for_storage`。

> 客户信息类工具若要开放，需把「角色」从模型参数改为**服务端按令牌映射**（P1 待办）。

## 超时与可用性

`mcp_servers/tool_timeout.py` 在 MCP 协议层给每次 `tools/call` 套 **30s 硬上限**
（`CONNECTOR_MAX_CALL_SECONDS`，默认 30）：超时返回可读 `ToolError`，满足平台「单次请求 30s 内响应」。
LLM 链路最坏延迟曾超过 30s，该硬上限即为其兜底。

## 版本声明

`minWorkbuddyVersion` 取所用新特性的**最高版本**：

| 使用到的特性 | 最低版本 |
| --- | --- |
| `mcp.json` 的 `disabledTools` | 4.22.15 |
| `name_zh/name_en`、`examples_zh/examples_en` | 4.24.0 |
| MCP 原生 OAuth（`auth_mode` 省略） | 基础 |

→ 因此 `connector-meta.json` 声明 **`"minWorkbuddyVersion": "4.24.0"`**。

## 提交前检查（对照官方清单）

- [x] 目录结构符合规范（meta + mcp + icon + skills）
- [x] `source` 为 kebab-case 且全局唯一（`enterprise-knowledge-assistant`）
- [x] 名称 / 说明 / 中英文示例完整（示例各 4 条）
- [x] 仅配置一个 MCP Server，远程地址用 HTTPS
- [x] MCP 原生 OAuth：元数据发现 + 动态注册 + PKCE 校验，接受约定回调
- [x] 未在任何文件中硬编码真实凭证（无凭证字段）
- [x] 图标：`icon.svg`（推荐）+ `icon.png` 512×512 透明
- [x] 版本号与 `minWorkbuddyVersion` 正确
- [x] Skill 覆盖工具用途、参数、示例、认证前置与错误恢复
- [x] 已隐藏写工具与不可信授权工具（最小权限）
- [x] 单次调用 30s 硬上限

## 提交前仍需完成（阻塞项）

1. **公网 HTTPS 端点**：把 `aggregate_server.py` 部署到可公网访问的 HTTPS 地址
   （反代 + 证书），然后替换 `mcp.json` 的 `url` 中的 `YOUR_DOMAIN`。平台建议可用性 ≥ 99.9%。
2. **OAuth 令牌策略**：确认线上 `data/oauth_state.json` 的持久化与备份策略（否则重启后需重新授权）。
3. **Skill `category`**：当前填 `04-DataAI`（官方《专家》`categoryId` 枚举，数据智能含知识管理/AI 应用）；
   因官方《技能》文档未公布独立枚举，提交前建议与运营再次确认。

## 合规材料（随包提交审核）

- [`docs/合规与数据说明.md`](../docs/合规与数据说明.md)：数据驻留（默认本地推理、不出网）、日志脱敏、隐私政策占位、凭证处理。
- [`docs/上架类目与资质.md`](../docs/上架类目与资质.md)：类目选择、主体资质与提交材料清单。

## 打包

```bash
cd connector && zip -r ../enterprise-knowledge-assistant.zip . -x '.*'
```
