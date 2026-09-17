"""Docs Server - 知识库规章制度检索（端口 8001）

- 从 data.py 读真实知识库文档
- RBAC：public 任何人 / internal 需登录 / confidential 需登录+部门精确匹配
- 混合检索：关键词加权（TF-IDF 风格） + 本地语义向量召回（RRF 融合）；
  embedding 后端不可用时自动回退纯关键词，行为与旧版一致。
"""
from fastmcp import FastMCP
import os
import re

from data_loader import load_documents, load_policy
from data_loader import DATA_DIR
import semantic_index

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
    """在规章制度知识库中检索文档（关键词 + 本地语义向量混合），含 RBAC 权限控制。
    密级：public(公开)/internal(内部需登录)/confidential(机密需本部门)。
    场景示例：'请假流程是什么？' '差旅报销标准？' '信息保密有什么规定？'"""
    if not query:
        return {"found": False, "message": "请输入查询内容"}

    query_lower = query.lower()

    # RBAC 前置：先在召回阶段过滤密级，机密文档绝不进入语义索引，避免侧信道泄漏。
    accessible, denied = [], []
    for doc in _documents():
        if _can_access(doc, user_department, is_authenticated):
            accessible.append(doc)
        else:
            denied.append(
                {"id": doc["id"], "title": doc["title"], "classification": doc["classification"]}
            )

    kw_scores = {d["id"]: _score_doc(d, query_lower) for d in accessible}
    kw_hits = {k: v for k, v in kw_scores.items() if v > 0}
    sem_hits = semantic_index.semantic_search(query, accessible)

    order = _fuse_ranks(kw_hits, sem_hits)

    by_id = {d["id"]: d for d in accessible}
    results = []
    for doc_id in order[:5]:
        doc = by_id.get(doc_id)
        if doc is None:
            continue
        sem = sem_hits.get(doc_id)
        results.append(
            {
                "id": doc["id"],
                "title": doc["title"],
                "content": _best_content(doc, query_lower, sem, max_snippet_chars),
                "classification": doc["classification"],
                "version": doc["version"],
                "last_updated": doc["last_updated"],
                "score": kw_scores.get(doc_id, 0),
                "semantic_score": round(sem["score"], 4) if sem else 0.0,
            }
        )

    return {
        "found": bool(results),
        "results": results,
        "denied": denied,
        "message": _build_message(results, denied, is_authenticated),
        "retrieval": "hybrid" if sem_hits else "keyword",
    }


def _fuse_ranks(kw_hits: dict, sem_hits: dict, k: int = 60) -> list:
    """RRF 融合关键词与语义两路排名；语义不可用时即关键词降序（与旧版一致）。"""
    if not sem_hits:
        return sorted(kw_hits, key=kw_hits.get, reverse=True)  # type: ignore[arg-type]
    kw_rank = {d: i for i, d in enumerate(sorted(kw_hits, key=kw_hits.get, reverse=True), 1)}  # type: ignore[arg-type]
    sem_rank = {d: i for i, d in enumerate(sorted(sem_hits, key=lambda d: sem_hits[d]["score"], reverse=True), 1)}
    ids = list(kw_rank) + [d for d in sem_rank if d not in kw_rank]
    far = 10 ** 6
    return sorted(ids, key=lambda d: 1 / (k + kw_rank.get(d, far)) + 1 / (k + sem_rank.get(d, far)), reverse=True)


def _best_content(doc: dict, query: str, sem: dict | None, max_chars: int) -> str:
    """优先用语义命中的块（问句同义复述时关键词块常切偏），否则回退关键词切块。"""
    if sem and sem.get("chunk"):
        chunk = sem["chunk"]
        if len(chunk) > max_chars:
            chunk = chunk[:max_chars]
        return chunk
    return _snippet_content(doc, query, max_chars)


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