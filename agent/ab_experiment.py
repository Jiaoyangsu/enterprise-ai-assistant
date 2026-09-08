"""AB 实验：混合架构 vs 全小模型 vs 全大模型，36 题 × 3 模式。

mode:
- 14b  : 全部问题强制用 qwen2.5:14b 跑 ReAct
- 32b  : 全部问题强制用 qwen2.5:32b 跑 ReAct
- mixed: 先 14b 裁判三态判定，再按 verdict 路由（14b/32b/reject）

每题记录 trace（工具调用序列），用 tests/test_questions.py 的
expected_tool + expected_key 自动判分：
- expected_tool == "refuse": 要求 无工具调用 且 answer 含拒绝语义
- 否则: 要求 实际调用过 expected_tool，且该工具观测中含 expected_key 字段，answer 非空

输出: agent/ab_report/<mode>.json + 控制台汇总。
"""
import argparse
import json
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from test_questions import get_questions  # noqa: E402
from react_agent import agent  # noqa: E402
from router import route  # noqa: E402
from run_routed import refuse  # noqa: E402

REFUSE_MARK = ("无法", "抱歉", "不能", "隐私", "无关", "对不起", "建议您", "系统")
NAMES = {"14b": ("qwen2.5:14b",), "32b": ("qwen2.5:32b",), "mixed": ()}


def check(q, trace, answer):
    """返回 (correct: bool, detail: str)。

    断言规则（由弱到强）：
    - expected_tool == "refuse": 无工具调用 且 answer 含拒绝语义
    - 基础: 实际调用过 expected_tool，且该工具观测含 expected_key 字段，answer 非空
    - expected_doc: search_knowledge_base 的观测中必须出现该文档 ID（支持 list）
    - expected_text: answer 必须包含该文本（支持 list，任一命中）
    """
    tools = [t["tool"] for t in trace]
    if q.expected_tool == "refuse":
        if not tools and any(m in answer for m in REFUSE_MARK):
            return True, "拒答无工具调用且语义正确"
        return False, f"期望拒答: tools={tools} answer={answer[:40]}"
    if q.expected_tool not in tools:
        return False, f"期望工具 {q.expected_tool}, 实际 {tools or '无'}; answer={answer[:40]}"
    if not answer or len(answer) < 2:
        return False, "answer 为空"

    obs_all = " ".join(t["obs"] for t in trace)
    base_ok = any(
        t["tool"] == q.expected_tool and q.expected_key in t["obs"] for t in trace
    )
    if not base_ok:
        return False, f"调用了 {q.expected_tool} 但观测未见 {q.expected_key}; answer={answer[:40]}"

    docs = [q.expected_doc] if isinstance(q.expected_doc, str) else q.expected_doc
    if docs and any(d not in obs_all for d in docs):
        miss = [d for d in docs if d not in obs_all]
        return False, f"搜索观测未命中期望文档 {miss}; 实际文档见 obs; answer={answer[:40]}"

    texts = [q.expected_text] if isinstance(q.expected_text, str) else q.expected_text
    if texts and not any(txt in answer for txt in texts):
        return False, f"answer 未含期望要点 {texts}; answer={answer[:60]}"

    return True, f"调用了 {q.expected_tool} 且观测含 {q.expected_key}"


def run(mode: str, questions, out_dir: str):
    model = NAMES[mode][0] if NAMES[mode] else None
    report = []
    print(f"\n===== mode={mode} model={model or 'mixed(judge路由)'}  {len(questions)}题 =====")
    for i, q in enumerate(questions, 1):
        print(f"  [{i}/{len(questions)}] {q.question[:30]}...", end=" ", flush=True)
        trace = []
        t0 = time.time()
        if mode == "mixed":
            r = route(q.question, strategy="mixed")
            m = r["model"]
            if r["route_type"] == "reject":
                ans = refuse(q.question, m)
            else:
                ans = agent(q.question, model=m, trace=trace)
        else:
            m = model
            ans = agent(q.question, model=m, trace=trace)
        elapsed = time.time() - t0
        ok, detail = check(q, trace, ans)
        print(f"{'OK ' if ok else 'XX '} ({elapsed:.0f}s)")
        report.append({
            "question": q.question,
            "group": q.group,
            "complexity": q.complexity,
            "expected_tool": q.expected_tool,
            "expected_key": q.expected_key,
            "expected_doc": q.expected_doc,
            "expected_text": q.expected_text,
            "model": m,
            "eval": ok,
            "detail": detail,
            "tools": [t["tool"] for t in trace],
            "answer": ans,
            "elapsed_s": round(elapsed, 1),
        })
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{mode}.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    ok = sum(1 for r in report if r["eval"])
    cost_s = sum(r["elapsed_s"] for r in report)
    print(f"== mode={mode}: {ok}/{len(report)}  总耗时{cost_s:.0f}s  工具分布={dict(Counter(r['model'] for r in report))}")
    return ok, len(report), cost_s, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="14b,32b,mixed")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    questions = get_questions()
    if args.limit:
        questions = questions[: args.limit]
    out_dir = os.path.join(HERE, "ab_report")
    results = {}
    for mode in args.modes.split(","):
        try:
            ok, total, cost, rep = run(mode, questions, out_dir)
            results[mode] = {"ok": ok, "total": total, "elapsed_s": round(cost, 1)}
        except Exception as e:
            print(f"mode={mode} 失败: {e}")
    print("\n" + "=" * 60)
    print(f"AB 汇总（判分标准: expected_tool/expected_key 自动断言）")
    for m, d in results.items():
        print(f"  {m:<6} 正确 {d['ok']}/{d['total']}   耗时 {d['elapsed_s']}s")
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()