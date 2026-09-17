"""混合检索测试：分块 / 向量召回 / RRF 融合 / RBAC 前置过滤 / embedding 降级。

embedding 后端（ollama bge-m3）不可用时，语义相关断言自动跳过，仅验证关键词路径与降级行为。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_servers"))

import semantic_index  # noqa: E402
from data_loader import load_documents  # noqa: E402


def load_server(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class R:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name, ok, detail=""):
        self.passed += 1 if ok else 0
        self.failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {name} {detail}")


def main():
    t = R()
    docs = load_documents()

    # ---- 分块 ----
    chunks = semantic_index.chunk_docs(docs)
    t.check("分块-数量>=文档数", len(chunks) >= len(docs), f"{len(chunks)}块/{len(docs)}文档")
    t.check("分块-块不超限", max(len(c["text"]) for c in chunks) <= semantic_index.CHUNK_CHARS + 200)
    t.check("分块-保留元数据", all(c["doc_id"] and c["title"] and c["classification"] for c in chunks))

    # ---- 余弦 ----
    t.check("余弦-正交为0", semantic_index._cosine([1, 0], [0, 1]) == 0)
    t.check("余弦-同向为1", abs(semantic_index._cosine([1, 1], [1, 1]) - 1) < 1e-9)

    # ---- RRF 融合 ----
    kw = {"A": 5, "B": 1}
    sem = {"B": {"score": 0.9}, "C": {"score": 0.8}}
    fused = load_server("docs_rrf", ROOT / "mcp_servers/docs_server.py")._fuse_ranks(kw, sem)
    t.check("融合-两路命中优先", fused[0] == "B", str(fused))
    t.check("融合-并集完整", set(fused) == {"A", "B", "C"}, str(fused))
    t.check("融合-无语义时按关键词", load_server("docs_kw", ROOT / "mcp_servers/docs_server.py")._fuse_ranks({"A": 1, "B": 2}, {})[0] == "B")

    # ---- docs_server 端到端（RBAC 前置 + 混合） ----
    docs_mod = load_server("docs_e2e", ROOT / "mcp_servers/docs_server.py")
    r = docs_mod.search_knowledge_base("请假流程", is_authenticated=True)
    t.check("检索-关键词召回", r["found"] and any("请假" in x["title"] for x in r["results"]), r["message"])
    t.check("检索-返回retrieval标记", r.get("retrieval") in ("hybrid", "keyword"), str(r.get("retrieval")))
    t.check("检索-结果含semantic_score字段", all("semantic_score" in x for x in r["results"]))

    # 机密文档未登录：必须出现在 denied，且绝不进入 results（含语义召回）
    r2 = docs_mod.search_knowledge_base("薪酬制度", is_authenticated=False)
    t.check("RBAC-机密未登录denied", any(d["title"] == "薪酬与绩效管理制度" for d in r2["denied"]))
    t.check("RBAC-机密未登录不进结果", not any(x["title"] == "薪酬与绩效管理制度" for x in r2["results"]))

    # ---- 语义召回（需要 embedding 后端） ----
    if not semantic_index.available():
        print("  ⚠️  embedding 后端不可用（ollama bge-m3 未就绪），跳过语义召回断言")
    else:
        # 同义复述：问「外出住宿能报多少钱」应召回差旅/报销类文档（关键词未必命中）
        accessible = [d for d in docs if docs_mod._can_access(d, "", True)]
        hits = semantic_index.semantic_search("外出住宿一晚最多能报多少钱", accessible, top_k=5)
        titles = {d["id"]: d["title"] for d in accessible}
        got = [titles.get(i, i) for i in hits]
        t.check("语义-同义复述召回", bool(hits), str(got))
        r3 = docs_mod.search_knowledge_base("出差住宿费上限是多少", is_authenticated=True)
        t.check("语义-端到端hybrid", r3.get("retrieval") == "hybrid", str(r3.get("retrieval")))
        t.check("语义-命中差旅/报销类", any(("差旅" in x["title"] or "报销" in x["title"]) for x in r3["results"]),
                str([x["title"] for x in r3["results"]]))

    print(f"\n结果：{t.passed}/{t.passed + t.failed} 通过，{t.failed} 失败")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()
