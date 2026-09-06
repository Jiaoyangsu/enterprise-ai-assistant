"""Docs Server - 知识库规章制度检索（端口 8001）"""
from fastmcp import FastMCP

mcp = FastMCP("docs")

# 知识库数据：文档 + 密级 + 适用部门
DOCUMENTS = [
    {
        "id": "DOC-001",
        "title": "员工请假管理制度",
        "content": "员工请假需提前一天提交申请，年假需提前三天申请。请假流程：填写申请单-部门审批-HR备案。",
        "keywords": ["请假", "年假", "申请", "审批"],
        "classification": "internal",
        "department": "全员",
    },
    {
        "id": "DOC-002",
        "title": "公司差旅报销标准",
        "content": "出差住宿标准：一线城市500元/晚，其他城市350元/晚。餐补100元/天。火车票二等座标准。",
        "keywords": ["差旅", "报销", "住宿", "餐补", "出差"],
        "classification": "internal",
        "department": "全员",
    },
    {
        "id": "DOC-003",
        "title": "技术部年度研发预算",
        "content": "技术部2024年度研发预算500万元，其中人员成本占60%，设备采购占25%，外包占15%。",
        "keywords": ["预算", "研发", "技术部", "成本"],
        "classification": "confidential",
        "department": "技术部",
    },
    {
        "id": "DOC-004",
        "title": "新员工入职流程",
        "content": "新员工入职需完成：签劳动合同、开通账号、参加入职培训。试用期三个月。",
        "keywords": ["入职", "新员工", "合同", "培训", "试用期"],
        "classification": "public",
        "department": "全员",
    },
    {
        "id": "DOC-005",
        "title": "考勤管理制度",
        "content": "上班时间9:00，下班18:00。迟到超过30分钟记一次。每月迟到三次以上影响绩效。",
        "keywords": ["考勤", "迟到", "上班", "打卡"],
        "classification": "internal",
        "department": "全员",
    },
]


@mcp.tool()
def search_knowledge_base(
    query: str,
    user_department: str = "",
    is_authenticated: bool = False,
) -> dict:
    """在规章制度知识库中检索文档（TF-IDF/关键词匹配）。包含 RBAC 权限控制。
    文档密级：public(公开)/internal(内部)/confidential(机密)。查询制度时使用。例如：请假流程是什么？"""
    if not query:
        return {"found": False, "message": "请输入查询内容"}

    query_lower = query.lower()
    results = []
    denied = []

    for doc in DOCUMENTS:
        # RBAC 拦截
        if not _can_access(doc, user_department, is_authenticated):
            denied.append({"id": doc["id"], "title": doc["title"]})
            continue

        score = _score_doc(doc, query_lower)
        if score > 0:
            results.append(
                {
                    "id": doc["id"],
                    "title": doc["title"],
                    "content": doc["content"],
                    "classification": doc["classification"],
                    "score": score,
                }
            )

    results.sort(key=lambda x: x["score"], reverse=True)

    return {
        "found": bool(results),
        "results": results[:3],
        "denied": denied,
        "message": _build_message(results, denied, is_authenticated),
    }


def _can_access(doc: dict, user_department: str, is_authenticated: bool) -> bool:
    classification = doc["classification"]
    if classification == "public":
        return True
    if classification == "internal":
        return is_authenticated
    # confidential：需身份 + 部门精确匹配
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
    for term in query.replace("，", " ").replace("，", " ").split():
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
