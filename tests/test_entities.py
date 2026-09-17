"""实体记忆 + 指代消解基线测试（entity_store / memory_server）。"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_servers"))

# 用临时记忆文件，避免污染 data/entities.json
os.environ["ENTITIES_FILE"] = os.path.join(tempfile.mkdtemp(prefix="ent_test_"), "entities.json")

import entity_store as es  # noqa: E402


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.notes = []

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        self.notes.append(f"{'✅' if ok else '❌'} {name} {detail}")


def names(hits):
    return {h["name"] for h in hits}


def main():
    t = TestResults()

    # ---- 索引规模 ----
    t.check("实体-索引规模", len(es.all_entities()) >= 400, str(len(es.all_entities())))

    # ---- 具名抽取 ----
    hits = es.extract_entities("陈志强在技术部", is_authenticated=True, user_role="staff")
    t.check("抽取-员工+部门", {"陈志强"} <= names(hits) and "技术部" in names(hits),
            str([(h["type"], h["name"]) for h in hits]))

    hits = es.extract_entities("华宇科技", is_authenticated=True, user_role="manager")
    t.check("抽取-长词优先(华宇科技整体命中)", names(hits) == {"华宇科技"}, str(names(hits)))

    # ---- RBAC ----
    t.check("RBAC-客户对普通员工隐藏",
            es.extract_entities("华宇科技", True, "staff") == [])
    t.check("RBAC-客户对管理层可见",
            "华宇科技" in names(es.extract_entities("华宇科技", True, "manager")))
    t.check("RBAC-未登录者不可见任何实体",
            es.extract_entities("陈志强", False, "manager") == [])

    # ---- 指代消解 ----
    r = es.resolve_entity("他", context_text="张伟在技术部", is_authenticated=True, user_role="staff")
    t.check("指代-他→焦点员工", r["resolved"] and r["entity"]["id"] == "emp:张伟", r.get("reason"))

    r = es.resolve_entity("那家客户", context_text="华宇科技的合同", is_authenticated=True, user_role="manager")
    t.check("指代-那家客户→焦点客户", r["resolved"] and r["entity"]["id"] == "cust:华宇科技", r.get("reason"))

    r = es.resolve_entity("那家客户", context_text="华宇科技的合同", is_authenticated=True, user_role="staff")
    t.check("指代-客户权限不足则不解", not r["resolved"])

    r = es.resolve_entity("这个部门", context_text="陈志强在技术部", is_authenticated=True, user_role="staff")
    t.check("指代-这个部门→焦点部门", r["resolved"] and r["entity"]["id"] == "dept:技术部", r.get("reason"))

    r = es.resolve_entity("HT-2024-001", is_authenticated=True, user_role="manager")
    t.check("具名-合同号解析", r["resolved"] and r["entity"]["id"] == "contract:HT-2024-001")

    # ---- 指代短语识别（防误命中）----
    t.check("代词-其他部门不误命中", es.find_pronouns("其他部门呢") == [])
    t.check("代词-他们公司识别为客户", any(p["type"] == "customer" for p in es.find_pronouns("他们公司")))
    t.check("代词-英文 data 不误命中 ta", es.find_pronouns("data status") == [])

    # ---- 长期记忆写入 / 删除 ----
    w = es.remember_entity("陈志强", "employee", aliases=["老陈"])
    t.check("记忆-写入别名", w["ok"] and os.path.exists(os.environ["ENTITIES_FILE"]), str(w))
    r = es.resolve_entity("老陈", is_authenticated=True, user_role="staff")
    t.check("记忆-别名可解析", r["resolved"] and r["entity"]["id"] == "emp:陈志强", r.get("reason"))

    d = es.forget_entity("emp:陈志强")
    t.check("记忆-删除别名", d["ok"])
    r = es.resolve_entity("老陈", is_authenticated=True, user_role="staff")
    t.check("记忆-删除后不再解析", not r["resolved"])

    print(f"\n结果：{t.passed}/{t.passed + t.failed} 通过，{t.failed} 失败")
    for n in t.notes:
        print(f"  {n}")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()
