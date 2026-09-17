"""Memory Server - 实体记忆与指代消解（端口 8004）

对外工具：
- resolve_entity   把"他/那家客户/这个部门/那份合同"等指代或具名，解析到规范实体
- extract_entities 抽取文本中的实体（按 RBAC 过滤）
- remember_entity  长期记忆写入：为实体追加别名/登记新实体（写操作，连接器不对外）
"""
from fastmcp import FastMCP

import entity_store as _es

mcp = FastMCP("memory")


@mcp.tool()
def resolve_entity(
    mention: str,
    context_text: str = "",
    type_hint: str = "",
    user_role: str = "",
    is_authenticated: bool = True,
) -> dict:
    """把问题里的指代或称呼解析成规范实体（用于指代消解）。
    mention：要解析的指代词或名称，如"他"、"那家客户"、"这个部门"、"HT-2024-001"。
    context_text：指代消解所依据的上下文（一般是最近几轮用户问题）；指代必须靠它定焦点。
    type_hint：可选，限定类型 employee/department/customer/contract。
    场景示例：'他年假还剩几天'（他→上轮提到的员工）、'那家客户合同什么状态'。"""
    return _es.resolve_entity(
        mention, context_text=context_text, type_hint=type_hint,
        is_authenticated=is_authenticated, user_role=user_role,
    )


@mcp.tool()
def extract_entities(text: str, user_role: str = "", is_authenticated: bool = True) -> dict:
    """抽取一段文本中出现的所有已知实体（员工/部门/客户/合同），按 RBAC 过滤。
    场景示例：'这段话提到哪些客户？' '从会议纪要里找出涉及的部门。'"""
    hits = _es.extract_entities(text, is_authenticated=is_authenticated, user_role=user_role)
    seen: list[dict] = []
    ids: set[str] = set()
    for h in hits:
        if h["id"] not in ids:
            ids.add(h["id"])
            seen.append({"id": h["id"], "type": h["type"], "name": h["name"]})
    return {"found": bool(seen), "count": len(seen), "entities": seen}


@mcp.tool()
def remember_entity(
    name: str,
    entity_type: str,
    aliases: str = "",
    attrs: str = "",
    user: str = "",
) -> dict:
    """长期记忆写入：给实体追加别名（逗号分隔）或登记新实体，落盘 data/entities.json。
    写入操作，供管理员/飞轮离线使用；连接器不对外暴露。
    场景示例：'把"老陈"记成陈志强的别名' → remember_entity("陈志强","employee",aliases="老陈")。"""
    alias_list = [a.strip() for a in str(aliases or "").replace("，", ",").split(",") if a.strip()]
    import json as _json

    attr_dict: dict = {}
    if attrs:
        try:
            loaded = _json.loads(attrs)
            if isinstance(loaded, dict):
                attr_dict = loaded
        except _json.JSONDecodeError:
            attr_dict = {}
    return _es.remember_entity(name, entity_type, aliases=alias_list, attrs=attr_dict, user=user)


if __name__ == "__main__":
    from connector import run

    run(mcp, port=8004)
