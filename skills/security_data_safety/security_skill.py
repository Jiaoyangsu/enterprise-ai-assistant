"""security_data_safety - 通用数据脱敏与敏感词风险审查（独立 Skill，纯标准库）

云沙箱直跑入口：python security_skill.py <tool> [--text ...] [--words ...]

工具：
  redact_pii           脱敏：手机号 / 身份证 / 邮箱 / 银行卡 / 统一社会信用代码
  risk_review_text     高危敏感词风险检查（敏感词可参数化）
  sanitize_for_storage 脱敏 + 风险检查两步合一，供入库前置清洗
"""
from __future__ import annotations

import argparse
import json
import re
import sys

DEFAULT_SENSITIVE_WORDS = [
    "竞品", "泄密", "薪资倒挂", "跳槽", "机密", "内幕", "裁员", "股票期权",
]

_MASKERS = [
    (
        "email",
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        lambda m: m.group(0)[:2] + "***@" + m.group(0).split("@", 1)[1],
    ),
    (
        "phone",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        lambda m: re.sub(r"(1\d\d)\d{4}(\d{4})", r"\1****\2", m.group(0)),
    ),
    (
        "id_card",
        re.compile(r"(?<!\d)(\d{6})(\d{8})([\dXx]{4})(?!\d)"),
        lambda m: m.group(1) + "********" + m.group(3),
    ),
    (
        "bank_card",
        re.compile(r"(?<!\d)(\d{4})(\d{8,12})(\d{4})(?!\d)"),
        lambda m: m.group(1) + "********" + m.group(3),
    ),
    (
        "credit_code",
        re.compile(r"(?<![0-9A-Za-z])([0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10})(?![0-9A-Za-z])"),
        lambda m: m.group(1)[:2] + "********" + m.group(1)[-2:],
    ),
]


def redact_pii(text: str) -> dict:
    """对文本中的手机号/身份证/邮箱/银行卡/统一社会信用代码脱敏。

    手机号 138****5678；身份证 110101********1234；
    邮箱 zh***@example.com；银行卡 6222********5678；
    统一社会信用代码 91**********XX。
    例如：把这段文字里的个人信息脱敏。"""
    if not text:
        return {"original": text, "redacted": "", "count": 0, "masked": {}}

    redacted = text
    masked = {}
    for name, pattern, repl in _MASKERS:
        found = pattern.findall(redacted)
        if found:
            masked[name] = len(found)
            redacted = pattern.sub(repl, redacted)

    return {
        "original": text,
        "redacted": redacted,
        "count": sum(masked.values()),
        "masked": masked,
    }


def risk_review_text(text: str, words: list[str] | None = None) -> dict:
    """检查文本是否包含高危敏感词（默认：竞品/泄密/薪资倒挂/跳槽/机密/内幕/裁员/股票期权）。

    可通过 words 参数传入自定义敏感词列表覆盖默认词表。
    返回命中的敏感词清单。例如：检查这段话有没有风险。"""
    if not text:
        return {"safe": True, "hits": [], "message": "文本为空，无风险"}

    word_list = list(words) if words else DEFAULT_SENSITIVE_WORDS
    hits = [w for w in word_list if w in text]
    return {
        "safe": not hits,
        "hits": hits,
        "message": "无敏感词" if not hits else f"命中敏感词：{'、'.join(hits)}",
    }


def sanitize_for_storage(text: str, keep_internal: bool = False) -> dict:
    """存储前清洗数据：先脱敏 PII，再检查敏感词。建议所有入库数据先走此工具。"""
    redacted = redact_pii(text)
    risk = risk_review_text(redacted["redacted"])
    return {
        "clean_text": redacted["redacted"],
        "pii_removed": redacted["count"],
        "masked": redacted["masked"],
        "risk": risk,
        "ready_for_storage": risk["safe"],
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="security_skill.py", description="数据脱敏与敏感词审查 Skill")
    parser.add_argument("tool", choices=["redact_pii", "risk_review_text", "sanitize_for_storage"])
    parser.add_argument("--text", default="", help="待处理文本")
    parser.add_argument("--words", default=None, help="自定义敏感词，逗号分隔（risk_review_text 用）")
    parser.add_argument("--input-json", action="store_true", help="从 stdin 读取 JSON 参数包（{tool,text,words}）")
    args = parser.parse_args(argv)

    try:
        if args.input_json:
            payload = json.load(sys.stdin)
            text = payload.get("text", "")
            words = payload.get("words")
        else:
            text = args.text
            words = args.words.split(",") if args.words else None

        if args.tool == "redact_pii":
            result = redact_pii(text)
        elif args.tool == "risk_review_text":
            result = risk_review_text(text, words=words)
        else:
            result = sanitize_for_storage(text)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 2

    print(json.dumps({"ok": True, "tool": args.tool, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())