"""Security Server - 隐私脱敏/风险审查（端口 8003）"""
import re
from fastmcp import FastMCP

from data_loader import load_sensitive_words, load_internal_names

mcp = FastMCP("security")

SENSITIVE_WORDS = load_sensitive_words()  # 可配置：data/config.json 的 sensitive_words
_INTERNAL_NAMES = sorted(load_internal_names(), key=len, reverse=True)  # 内部人名（PII）

# 地址：保留行政区（省/市/区/县），掩码门牌明细
_ADDR_RE = re.compile(
    r"((?:[\u4e00-\u9fa5]{2,}(?:省|市|区|县)))"
    r"([\u4e00-\u9fa5A-Za-z0-9]{2,}(?:路|街|街道|巷|号|栋|幢|单元|室|楼))"
)
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def _mask_name(name: str) -> str:
    """姓名脱敏：保留姓，其余以 * 代替（3 字 -> 姓**）。"""
    if len(name) <= 1:
        return name
    return name[0] + "*" * (len(name) - 1)


@mcp.tool()
def redact_pii(text: str) -> dict:
    """对文本中的敏感个人信息脱敏：手机号、身份证、邮箱、银行卡、地址、
    内部人名。手机号保留前后4位；身份证保留6+4；银行卡保留前4后4；
    邮箱仅保留首字符；地址保留行政区、掩码门牌；姓名保留姓。
    例如：把手机号、邮箱、地址、客户姓名脱敏。"""
    if not text:
        return {"original": text, "redacted": "", "count": 0, "types": {}}
    return _redact(text)


def _redact(text: str) -> dict:
    redacted = text
    counts: dict[str, int] = {}

    def bump(kind: str):
        counts[kind] = counts.get(kind, 0) + 1

    # 身份证：110101199001011234 -> 110101********1234
    def mask_id(m):
        bump("idcard")
        return re.sub(r"(\d{6})\d{8}(\d{4})", r"\1********\2", m.group(0))

    redacted = re.sub(r"(?<!\d)(\d{17}[\dXx])(?!\d)", mask_id, redacted)

    # 银行卡：16~19 位数字 -> 前4后4，中间掩码
    def mask_bank(m):
        bump("bankcard")
        s = m.group(0)
        return s[:4] + "*" * (len(s) - 8) + s[-4:]

    redacted = re.sub(r"(?<!\d)(\d{16,19})(?!\d)", mask_bank, redacted)

    # 手机号：13812345678 -> 138****5678
    def mask_phone(m):
        bump("phone")
        return re.sub(r"(\d{3})\d{4}(\d{4})", r"\1****\2", m.group(0))

    redacted = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", mask_phone, redacted)

    # 邮箱：zhang@corp.com -> z***@corp.com
    def mask_email(m):
        bump("email")
        return f"{m.group(1)}***@{m.group(2)}"

    redacted = _EMAIL_RE.sub(mask_email, redacted)

    # 地址：北京市朝阳区建国路88号 -> 北京市朝阳区******
    def mask_addr(m):
        bump("address")
        return m.group(1) + "*" * len(m.group(2))

    redacted = _ADDR_RE.sub(mask_addr, redacted)

    # 内部人名：张伟 -> 张*
    for name in _INTERNAL_NAMES:
        if name and name in redacted:
            redacted = redacted.replace(name, _mask_name(name))
            counts["name"] = counts.get("name", 0) + 1

    return {
        "original": text,
        "redacted": redacted,
        "count": sum(counts.values()),
        "types": counts,
    }


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
    from connector import run

    run(mcp, port=8003)
