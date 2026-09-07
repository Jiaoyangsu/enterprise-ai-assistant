"""Security Server - 隐私脱敏/风险审查（端口 8003）"""
import re
from fastmcp import FastMCP

mcp = FastMCP("security")

SENSITIVE_WORDS = ["竞品", "泄密", "薪资倒挂", "跳槽", "机密", "内幕", "裁员", "股票期权"]


@mcp.tool()
def redact_pii(text: str) -> dict:
    """对文本中的手机号和身份证号进行脱敏（保留前后4位/6+4结构）。
    手机号：138****5678；身份证：110101********1234。例如：把手机号脱敏。"""
    if not text:
        return {"original": text, "redacted": "", "count": 0}

    redacted = text
    count = 0

    # 手机号：13812345678 -> 138****5678
    def mask_phone(m):
        nonlocal count
        count += 1
        return re.sub(r"(\d{3})\d{4}(\d{4})", r"\1****\2", m.group(0))

    redacted = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", mask_phone, redacted)

    # 身份证：110101199001011234 -> 110101********1234
    def mask_id(m):
        nonlocal count
        count += 1
        return re.sub(r"(\d{6})\d{8}(\d{4})", r"\1********\2", m.group(0))

    redacted = re.sub(r"(?<!\d)(\d{17}[\dXx])(?!\d)", mask_id, redacted)

    return {"original": text, "redacted": redacted, "count": count}


@mcp.tool()
def risk_review_text(text: str) -> dict:
    """检查文本中是否包含高危敏感词（竞品/泄密/薪资倒挂等）。
    返回命中的敏感词清单。例如：检查这段话有没有风险。"""
    if not text:
        return {"safe": True, "hits": [], "message": "文本为空，无风险"}

    hits = [w for w in SENSITIVE_WORDS if w in text]
    return {
        "safe": not hits,
        "hits": hits,
        "message": "无敏感词" if not hits else f"命中敏感词：{'、'.join(hits)}",
    }


@mcp.tool()
def sanitize_for_storage(text: str, keep_internal: bool = False) -> dict:
    """存储前清洗数据：先脱敏 PII，再检查敏感词。建议所有入库数据先走此工具。"""
    redacted = redact_pii(text)
    risk = risk_review_text(redacted["redacted"])
    return {
        "clean_text": redacted["redacted"],
        "pii_removed": redacted["count"],
        "risk": risk,
        "ready_for_storage": risk["safe"],
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8003)
