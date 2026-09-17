---
name: security_data_safety
slug: security-data-safety
displayName: 数据脱敏与敏感词审查
version: 1.0.0
summary: 对文本中的手机号/身份证/邮箱/银行卡/统一社会信用代码脱敏，并检查高危敏感词（词表可自定义），输出是否可入库的合规结论。
description: |
  通用数据合规技能：对任意文本执行 PII 脱敏（手机号、身份证、邮箱、银行卡、统一社会信用代码）和高危敏感词风险审查（默认词表：竞品/泄密/薪资倒挂/跳槽/机密/内幕/裁员/股票期权，可通过 words 参数自定义覆盖）。无外部网络依赖、纯标准库实现，可在云沙箱内直接运行。
  适用于员工数据处理、日志/数据库入库前清洗、内容合规审查等场景。
tags:
  - 脱敏
  - 敏感词
  - 合规
  - PII
  - privacy
license: MIT
homepage: https://example.com/security-data-safety
---

# security-data-safety

通用数据合规技能：PII 脱敏 + 敏感词风险审查。无外部网络依赖，纯标准库实现，可在沙箱内直接运行。

## 工具

| 工具 | 说明 | 关键入参 |
|------|------|----------|
| `redact_pii` | 脱敏手机号/身份证/邮箱/银行卡/统一社会信用代码 | `text` |
| `risk_review_text` | 高危敏感词检查，词表可自定义覆盖 | `text`, `words`(可选) |
| `sanitize_for_storage` | 入库前置清洗：先脱敏再查敏感词，输出是否可入库 | `text` |

## 调用方式

```bash
python security_skill.py redact_pii --text "联系人 13812345678"
python security_skill.py risk_review_text --text "我们拿到了竞品的清单" --words "竞品,内幕"
python security_skill.py sanitize_for_storage --text "电话13800001111，讨论薪资倒挂"
```

或通过 JSON 参数包：

```bash
echo '{"text":"联系人 13812345678","tool":"redact_pii"}' | python security_skill.py redact_pii --input-json
```

## 输出结构

```json
{"ok": true, "tool": "redact_pii", "result": {
  "original": "联系人 13812345678",
  "redacted": "联系人 138****5678",
  "count": 1,
  "masked": {"phone": 1}
}}
```

## 简单改造为 Pay Skill

将工具函数封装为可调用服务时，保留验证逻辑即可，业务侧无数据绑定：

- `redact_pii` / `sanitize_for_storage` 为无状态纯函数，天然幂等，适合按次计费。
- 敏感词表由调用方通过 `words` 参数传入，技能不内置任何企业专属字段。