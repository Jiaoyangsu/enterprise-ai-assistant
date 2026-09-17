"""实体记忆与指代消解（单一实现，供 memory_server / react_agent 共用）。

设计：
- **实体来源 = 业务数据层**（`data.py` 的员工/部门/客户/合同）——只建索引，不复制敏感字段；
  客户/合同实体只存名称/编号/状态/金额等，手机号/身份证一律不进实体表。
- **长期记忆落点 `data/entities.json`**（可用 `ENTITIES_FILE` 覆盖）：只存"学到的别名/新实体"，
  内置实体不落盘（避免与 `data_generated.py` 双份漂移）；文件缺失即纯内置索引。
- **RBAC**：客户/合同实体仅 `policy.customer_visible_roles` 可见；员工/部门需已登录。
  与 `ops_server._customer_role_allowed` 同源（都读 `data/policy.json`）。
- **指代消解**：`resolve_entity(mention, context_text=...)` 先用别名精确匹配；命中代词时，
  在上下文里取**同类型最后出现的实体**作为焦点（如"他"→最近提到的员工）。
"""
import json
import os
import re
from functools import lru_cache

from data import CONTRACTS, CUSTOMERS, DEPARTMENTS, EMPLOYEES
from data_loader import load_policy

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_MEMORY = os.path.join(_ROOT, "data", "entities.json")

_TYPE_PREFIX = {
    "employee": "emp",
    "department": "dept",
    "customer": "cust",
    "contract": "contract",
}
_CUSTOMER_CLASS = {"customer", "contract"}

# 指代短语 → 目标实体类型（长词优先匹配，避免"他们公司"被"他们"截断）
PRONOUNS: list[tuple[str, str]] = [
    ("该员工", "employee"), ("这名员工", "employee"), ("这位同事", "employee"),
    ("那位同事", "employee"), ("他本人", "employee"), ("这个人", "employee"),
    ("那个人", "employee"), ("那位", "employee"), ("此人", "employee"),
    ("他", "employee"), ("她", "employee"), ("ta", "employee"),
    ("他们公司", "customer"), ("该公司", "customer"), ("那家公司", "customer"),
    ("这家公司", "customer"), ("该企业", "customer"), ("这家企业", "customer"),
    ("该客户", "customer"), ("那家客户", "customer"), ("这个客户", "customer"),
    ("那个客户", "customer"),
    ("该部门", "department"), ("这个部门", "department"), ("那个部门", "department"),
    ("本部门", "department"), ("该团队", "department"), ("这个团队", "department"),
    ("该合同", "contract"), ("这个合同", "contract"), ("那个合同", "contract"),
    ("这份合同", "contract"), ("那份合同", "contract"), ("这笔合同", "contract"),
    ("这张合同", "contract"), ("该单子", "contract"), ("这张单子", "contract"),
]
_PRONOUNS_SORTED = sorted(PRONOUNS, key=lambda x: len(x[0]), reverse=True)


def _memory_path() -> str:
    return os.environ.get("ENTITIES_FILE", _DEFAULT_MEMORY)


def load_memory() -> dict:
    """读取长期实体记忆（学到的别名/新实体）；无文件或损坏返回空壳。"""
    try:
        with open(_memory_path(), encoding="utf-8") as f:
            mem = json.load(f)
        if isinstance(mem, dict):
            mem.setdefault("aliases", {})
            mem.setdefault("extra", [])
            return mem
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "aliases": {}, "extra": []}


def _save_memory(mem: dict) -> bool:
    path = _memory_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _seed_entities() -> list[dict]:
    """从业务数据层构造内置实体（非敏感字段）。"""
    ents: list[dict] = []
    for name, e in EMPLOYEES.items():
        ents.append({
            "id": f"emp:{name}", "type": "employee", "name": name, "aliases": [],
            "attrs": {"department": e.get("department", ""), "position": e.get("position", "")},
        })
    for d in DEPARTMENTS:
        aliases = [d["code"]] if d.get("code") else []
        ents.append({
            "id": f"dept:{d['name']}", "type": "department", "name": d["name"], "aliases": aliases,
            "attrs": {"bg": d.get("bg", ""), "manager": d.get("manager", "")},
        })
    for name, c in CUSTOMERS.items():
        ents.append({
            "id": f"cust:{name}", "type": "customer", "name": name, "aliases": [],
            "attrs": {"industry": c.get("industry", ""), "level": c.get("level", "")},
        })
    for cid, c in CONTRACTS.items():
        ents.append({
            "id": f"contract:{cid}", "type": "contract", "name": cid, "aliases": [],
            "attrs": {"customer": c.get("customer", ""), "status": c.get("status", "")},
        })
    return ents


def _memory_signature() -> tuple:
    try:
        st = os.stat(_memory_path())
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


@lru_cache(maxsize=8)
def _entities_cached(sig: tuple) -> tuple[dict, ...]:
    entities = _seed_entities()
    mem = load_memory()
    by_id = {e["id"]: e for e in entities}
    for eid, aliases in (mem.get("aliases") or {}).items():
        if eid in by_id and isinstance(aliases, list):
            seen = set(by_id[eid]["aliases"])
            for a in aliases:
                if a and a not in seen:
                    by_id[eid]["aliases"].append(a)
                    seen.add(a)
    for ex in mem.get("extra") or []:
        if isinstance(ex, dict) and ex.get("id") and ex.get("name"):
            by_id.setdefault(
                ex["id"],
                {"id": ex["id"], "type": ex.get("type", "custom"), "name": ex["name"],
                 "aliases": list(ex.get("aliases") or []), "attrs": dict(ex.get("attrs") or {})},
            )
    return tuple(by_id.values())


def all_entities() -> list[dict]:
    """内置实体 + 长期记忆中的别名/新实体（文件变化自动重载）。"""
    return list(_entities_cached(_memory_signature()))


def entity_by_id(eid: str) -> dict | None:
    return next((e for e in all_entities() if e["id"] == eid), None)


def _norm(s: str) -> str:
    return str(s or "").strip().lower()


def _alias_pairs() -> list[tuple[str, dict]]:
    """(别名, 实体) 列表，按别名长度降序（长别名优先，避免"华宇"盖过"华宇科技"）。"""
    pairs: list[tuple[str, dict]] = []
    for e in all_entities():
        for a in [e["name"], *e.get("aliases", [])]:
            if a and len(str(a).strip()) >= 2:
                pairs.append((str(a).strip(), e))
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return pairs


def _customer_roles() -> set:
    return load_policy()["customer_visible_roles"]


def _visible(ent: dict, is_authenticated: bool, user_role: str) -> bool:
    if ent["type"] in _CUSTOMER_CLASS:
        return bool(is_authenticated) and user_role in _customer_roles()
    return bool(is_authenticated)


def _find_all(text: str, alias: str) -> list[tuple[int, int]]:
    """在 text 中定位 alias 的所有出现（ASCII 别名要求词边界，避免 CS/HR 误命中）。"""
    low = text.lower()
    al = alias.lower()
    out: list[tuple[int, int]] = []
    if al.isascii():
        for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(al)}(?![A-Za-z0-9])", low):
            out.append(m.span())
    else:
        start = 0
        while True:
            i = low.find(al, start)
            if i < 0:
                break
            out.append((i, i + len(al)))
            start = i + 1
    return out


def extract_entities(text: str, is_authenticated: bool = True, user_role: str = "") -> list[dict]:
    """抽取文本中出现的实体（最长匹配、不重叠、按出现位置排序，已按 RBAC 过滤）。"""
    if not text:
        return []
    taken = [False] * len(text)
    found: list[dict] = []
    for alias, ent in _alias_pairs():
        for s, e in _find_all(text, alias):
            if any(taken[s:e]):
                continue
            for i in range(s, e):
                taken[i] = True
            found.append({
                "id": ent["id"], "type": ent["type"], "name": ent["name"],
                "alias": alias, "start": s, "end": e,
            })
    found.sort(key=lambda x: x["start"])
    return [f for f in found if _visible(entity_by_id(f["id"]), is_authenticated, user_role)]


def focus_entity(context_text: str, entity_type: str,
                 is_authenticated: bool = True, user_role: str = "") -> dict | None:
    """上下文里同类型实体中**最后出现**的那个（指代焦点）。"""
    hits = [h for h in extract_entities(context_text, is_authenticated, user_role)
            if h["type"] == entity_type]
    if not hits:
        return None
    last = hits[-1]
    ent = entity_by_id(last["id"])
    if not ent:
        return None
    return {"id": ent["id"], "type": ent["type"], "name": ent["name"], "alias": last["alias"]}


def _pronoun_regex(phrase: str) -> str:
    """指代短语的匹配正则：ASCII 加词边界；'他/她' 排除'其他/他们'等误命中。"""
    if phrase.isascii():
        return rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])"
    if phrase in ("他", "她"):
        return rf"(?<!其){re.escape(phrase)}(?!们)"
    return re.escape(phrase)


def find_pronouns(text: str) -> list[dict]:
    """找出文本中的指代短语及其目标类型（长词优先，同一短语只报首次）。"""
    if not text:
        return []
    low = str(text).lower()
    hits: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for phrase, etype in _PRONOUNS_SORTED:
        if phrase in seen:
            continue
        m = re.search(_pronoun_regex(phrase), low)
        if m:
            seen.add(phrase)
            hits.append((m.start(), {"mention": phrase, "type": etype}))
    hits.sort(key=lambda x: x[0])
    return [h for _, h in hits]


def resolve_entity(mention: str, context_text: str = "", type_hint: str = "",
                   is_authenticated: bool = True, user_role: str = "") -> dict:
    """把 mention（具名或指代）解析到规范实体。

    返回 {resolved, entity, candidates, reason}；candidates 为可见候选（≤5）。
    """
    m = str(mention or "").strip()
    if not m:
        return {"resolved": False, "entity": None, "candidates": [], "reason": "mention 为空"}

    # 1) 具名精确匹配（mention 本身含实体名/别名）
    direct = extract_entities(m, is_authenticated, user_role)
    if direct:
        ent = entity_by_id(direct[0]["id"])
        return {"resolved": True, "entity": ent, "candidates": [ent], "reason": "具名精确匹配"}

    # 2) 指代消解：确定目标类型 → 取上下文焦点
    ptype = next((p["type"] for p in find_pronouns(m)), "")
    target_type = type_hint or ptype
    if target_type:
        scope = (m + " " + context_text).strip() if context_text else m
        focus = focus_entity(scope, target_type, is_authenticated, user_role)
        if focus:
            ent = entity_by_id(focus["id"])
            return {"resolved": True, "entity": ent, "candidates": [ent],
                    "reason": f"指代消解（{target_type} 焦点）"}

    # 3) 兜底：返回该类型可见候选，供上层澄清
    cands = [e for e in all_entities()
             if (not target_type or e["type"] == target_type) and _visible(e, is_authenticated, user_role)]
    return {"resolved": False, "entity": None, "candidates": cands[:5],
            "reason": "未命中实体或指代焦点"}


def remember_entity(name: str, entity_type: str, aliases: list[str] | None = None,
                    attrs: dict | None = None, user: str = "") -> dict:
    """长期记忆写入：给已有实体加别名，或登记新实体（落盘 `data/entities.json`）。"""
    name = str(name or "").strip()
    entity_type = str(entity_type or "").strip() or "custom"
    if not name:
        return {"ok": False, "message": "name 不能为空"}
    aliases = [str(a).strip() for a in (aliases or []) if str(a).strip()]
    mem = load_memory()
    eid = f"{_TYPE_PREFIX.get(entity_type, entity_type)}:{name}"
    known = entity_by_id(eid)
    if known:
        cur = mem["aliases"].setdefault(eid, [])
        for a in aliases:
            if a not in cur and a != name:
                cur.append(a)
        reason = "已为既有实体追加别名"
    else:
        mem["extra"].append({
            "id": eid, "type": entity_type, "name": name, "aliases": aliases,
            "attrs": dict(attrs or {}), "source": user or "learned",
        })
        reason = "已登记新实体"
    ok = _save_memory(mem)
    _entities_cached.cache_clear()
    return {"ok": ok, "id": eid, "aliases": aliases, "reason": reason}


def forget_entity(eid: str) -> dict:
    """删除长期记忆中的实体/别名（合规删除用；内置实体不可删）。"""
    mem = load_memory()
    mem.get("aliases", {}).pop(eid, None)
    before = len(mem.get("extra", []))
    mem["extra"] = [e for e in mem.get("extra", []) if e.get("id") != eid]
    ok = _save_memory(mem)
    _entities_cached.cache_clear()
    return {"ok": ok, "removed": eid, "was_extra": before != len(mem["extra"])}
