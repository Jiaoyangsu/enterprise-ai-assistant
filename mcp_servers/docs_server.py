"""Docs Server - 知识库规章制度检索（端口 8001）

- 从 data.py 读真实知识库文档
- RBAC：public 任何人 / internal 需登录 / confidential 需登录+部门精确匹配
- TF-IDF 打分 + 关键词加权
"""
from fastmcp import FastMCP
import os
import re

from data_loader import load_documents, load_policy
from data_loader import DATA_DIR

mcp = FastMCP("docs")

DOCUMENTS = load_documents()  # 启动时加载（下方 _documents() 会按文件 mtime 热重载）

# 知识库热重载：飞轮把新知识写入 data/documents.json 后，无需重启本服务即可检索到。
_DOC_PATH = os.path.join(DATA_DIR, "documents.json")
_doc_cache = {"mtime": None, "docs": DOCUMENTS}


def _documents() -> list:
    """返回最新文档集；若 data/documents.json 被改动（mtime 变化）则重载。"""
    try:
        mt = os.path.getmtime(_DOC_PATH)
    except OSError:
        mt = None
    if mt != _doc_cache["mtime"]:
        _doc_cache["docs"] = load_documents()
        _doc_cache["mtime"] = mt
    return _doc_cache["docs"]


@mcp.tool()
def search_knowledge_base(
    query: str,
    user_department: str = "",
    is_authenticated: bool = False,
    max_snippet_chars: int = 600,
) -> dict:
    """在规章制度知识库中检索文档（TF-IDF/关键词匹配），含 RBAC 权限控制。
    密级：public(公开)/internal(内部需登录)/confidential(机密需本部门)。
    场景示例：'请假流程是什么？' '差旅报销标准？' '信息保密有什么规定？'"""
    if not query:
        return {"found": False, "message": "请输入查询内容"}

    query_lower = query.lower()
    results = []
    denied = []

    for doc in _documents():
        if not _can_access(doc, user_department, is_authenticated):
            denied.append(
                {"id": doc["id"], "title": doc["title"], "classification": doc["classification"]}
            )
            continue

        score = _score_doc(doc, query_lower)
        if score > 0:
            results.append(
                {
                    "id": doc["id"],
                    "title": doc["title"],
                    "content": _snippet_content(doc, query_lower, max_snippet_chars),
                    "classification": doc["classification"],
                    "version": doc["version"],
                    "last_updated": doc["last_updated"],
                    "score": score,
                }
            )

    results.sort(key=lambda x: x["score"], reverse=True)

    return {
        "found": bool(results),
        "results": results[:5],
        "denied": denied,
        "message": _build_message(results, denied, is_authenticated),
    }


def _can_access(doc: dict, user_department: str, is_authenticated: bool) -> bool:
    """按 policy 的 docs_visibility 决定可见性（密级规则可定制）。

    policy 结构（data/policy.json, rbac.docs_visibility）：
      min_auth: 是否要求已登录
      departments: 允许访问的部门列表；空列表表示"不限制部门"，
        但 confidential 密级在未显式放开时默认仍需本部门精确匹配（保安全）。
    """
    classification = doc["classification"]
    vis = load_policy()["docs_visibility"].get(classification, {})
    if vis.get("min_auth") is False:
        return True
    if not is_authenticated:
        return False
    depts = vis.get("departments") or []
    if not depts:
        return classification != "confidential" or user_department == doc["department"]
    return user_department in depts


def _snippet_content(doc: dict, query: str, max_chars: int = 600) -> str:
    """从文档正文中切出与 query 最相关的段落，避免把整篇长文喂给 LLM。

    按"条"（第X条）切段；命中 query 关键词的段优先；都不中则取开头。
    返回拼接后的字符串，控制在 max_chars 内，保留段落上下文方便模型作答。
    """
    content = doc.get("content", "")
    if len(content) <= max_chars:
        return content

    # 按句子/条目切分候选块
    cands = re.split(r"(?<=[。；])", content)
    chunks: list[str] = []
    buf = ""
    for c in cands:
        buf += c
        if len(buf) >= 60 or c.endswith("。"):
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)

    terms = [t for t in re.split(r"[，。\s]+", query) if len(t) >= 2]
    # 中文 query 无标点时按 bigram 拆词，提升短句式内嵌匹配
    if len(terms) <= 1 and len(query) >= 4:
        terms = [query[i : i + 2] for i in range(len(query) - 1)]
    scored = []
    for i, ch in enumerate(chunks):
        hits = sum(1 for t in terms if t in ch)
        # 命中词多的块，附前后各一块作上下文
        if hits:
            start = max(0, i - 1)
            end = min(len(chunks), i + 2)
            scored.append((-hits, start, end))
    if scored:
        scored.sort(key=lambda x: (x[0], x[1]))
        _, start, end = scored[0]
        picked = "".join(chunks[start:end])
    else:
        picked = "".join(chunks[:3])

    if len(picked) <= max_chars:
        return picked
    return picked[:max_chars]


def _score_doc(doc: dict, query: str) -> int:
    score = 0
    haystack = (
        doc["content"].lower()
        + " ".join(doc["keywords"]).lower()
        + doc["title"].lower()
    )
    for kw in doc["keywords"]:
        if kw in query:
            score += 3
    for term in query.replace("，", " ").replace("。", " ").split():
        if term and term in haystack:
            score += 2
    return score


def _build_message(results, denied, is_authenticated) -> str:
    parts = []
    if results:
        parts.append(f"找到 {len(results)} 篇相关文档")
    else:
        parts.append("未找到相关文档")
    if denied:
        names = "、".join(d["title"] for d in denied)
        if not is_authenticated:
            parts.append(f"{names} 需登录后方可查看")
        else:
            parts.append(f"{names} 无权访问（密级/部门不匹配）")
    return "；".join(parts)


if __name__ == "__main__":
    from connector import run

    run(mcp, port=8001)