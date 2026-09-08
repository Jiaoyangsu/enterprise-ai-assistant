# 变更日志

> 规范：每次开发在 `dev` 分支提交并 push（每日同步）；验证稳定的改动合并到 `main` 并 push。
> 同步记录对标 `git log --oneline`。

## 2026-09-08

### AB 实验结论（决定默认模型）
- 36 题 × 三模式对照（全14b / 混合 / 全32b，判分=expected_tool/expected_key 自动断言）：
  - 全 14b：35/36 (97%)，2.6 分钟 —— **最优**
  - 混合：32/36 (89%)，11.3 分钟
  - 全 32b：31/36 (86%)，15.9 分钟
- 结论：当前题目为"工具遵循型"任务，14b 最优；32b 在简单任务上易"自作主张"（拒绝代提交/跳过工具/选错工具）。
- **默认模型改为 14b**；混合路由（judge 三态判定）保留代码但生产弃用。

### 三态路由（实验能力，默认关闭）
- `complexity_judge.py`：judge 三态 `simple/complex/reject` + few-shot 校准（36 题黄金集 89% 一致；`gold_routing.py`）。
- `router.py`：默认策略 `14b`（不做判定）；`mixed` 策略保留三态路由。

### Persona 优化（5 项）
1. 身份会话化：身份仅用于填充工具参数，不影响工具选择/判定（SYSTEM 措辞）。
2. 工具映射加"优先尝试/承认缺字段"兜底。
3. 拒答规则与模板收敛到单一来源 `boundaries.py`（react_agent / judge / reject 三处引用）。
4. Few-shot ×6：单查/组合/写操作/代提交/年假不足/脱敏+风险一体化/陷阱题。
5. 写操作：明确"可代同事提交，须明确意图"。
6. 新增"必须基于真实数据"陷阱原则（"别查了"也要先走工具）。
- 优化后 14b 36 题 = 34/36（剩余 2 题为判分口径差，语义均正确）。

### 基础设施
- `ab_experiment.py` + `ab_monitor.py`：AB 实验与 5 分钟进度监控。
- `react_agent.agent` 增加 `trace` 支持（AB 判分用）。
- AutoDL 新实例（connect.cqa1.seetacloud.com:41036）：模型在 `/root/autodl-tmp/ollama/models`（14b+32b），SSH 隧道 + keepalive 脚本 `scripts/tunnel_ollama.sh`。

### 知识库文档补全（+12 篇实操指南）
- 新增 `mcp_servers/documents_extra.py`：12 篇"真实企业会用"的操作文档（每篇含时限/金额/审批链等细节，供综合推理引用）：
  DOC-101 新员工入职培训指南 / DOC-102 费用报销操作流程 / DOC-103 请假提交与审批操作流程 / DOC-104 加班申请与调休操作流程 / DOC-105 差旅预订与出行指南 / DOC-106 考勤打卡与异常处理指南 / DOC-107 离职交接办理指南 / DOC-108 各类证明开具指南 / DOC-109 办公用品与固定资产申领指南 / DOC-110 信息安全日常操作手册 / DOC-111 劳动合同签订与变更操作流程 / DOC-112 员工考核与晋升流程。
- 每篇带 `keywords/classification/department/version/last_updated` 元数据，与制度文档同构（搜索打分 + RBAC 依赖）。
- `documents.py` 尾部 `DOCUMENTS.extend(EXTRA_DOCUMENTS)`，知识库 15→27 篇。搜索层已验证：新入职/报销/加班/差旅查询均命中新增文档，confidential RBAC 正常。
- 新增本地 Web 前端 `agent/web_app.py`（零依赖 http.server，端口 8787，POST /api/chat → agent 14b）。