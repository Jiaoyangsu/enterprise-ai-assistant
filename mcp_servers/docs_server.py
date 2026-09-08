"""Docs Server - 知识库规章制度检索（端口 8001）

- 从 data.py 读真实知识库文档
- RBAC：public 任何人 / internal 需登录 / confidential 需登录+部门精确匹配
- TF-IDF 打分 + 关键词加权
"""
from fastmcp import FastMCP

from data import DOCUMENTS

mcp = FastMCP("docs")


@mcp.tool()
def search_knowledge_base(
    query: str,
    user_department: str = "",
    is_authenticated: bool = False,
) -> dict:
    """在规章制度知识库中检索文档（TF-IDF/关键词匹配），含 RBAC 权限控制。
    密级：public(公开)/internal(内部需登录)/confidential(机密需本部门)。
    场景示例：'请假流程是什么？' '差旅报销标准？' '信息保密有什么规定？'"""
    if not query:
        return {"found": False, "message": "请输入查询内容"}

    query_lower = query.lower()
    results = []
    denied = []

    for doc in DOCUMENTS:
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
                    "content": doc["content"],
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
    classification = doc["classification"]
    if classification == "public":
        return True
    if classification == "internal":
        return is_authenticated
    if classification == "confidential":
        return is_authenticated and user_department == doc["department"]
    return False


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
    mcp.run(transport="streamable-http", port=8001)