"""模型自选路由批量测试：36 题全量跑（三态: simple/complex/reject）。

流程（每题独立）：
1. 模型（14b 裁判）自己判定复杂度 verdict = simple|complex|reject + reason
2. 路由器按 verdict 选路由：simple -> 14b ReAct，complex -> 32b ReAct，reject -> 14b 直接拒答
3. 用选中模型回答（复杂走 ReAct 调真实工具）
4. 展示：判定 + reason + 选中模型 + 完整答案
5. 与黄金集比一致率（gold_routing.py）

注意：判题的不是我、也不是规则，是模型自己。few-shot 校准只教裁判"业务语义"。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from test_questions import get_questions  # noqa: E402
from router import route  # noqa: E402
from react_agent import agent  # noqa: E402
from gold_routing import gold_map  # noqa: E402
from boundaries import BOUNDARY_RULES  # noqa: E402

REJECT_PROMPT = f"""你是企业AI助手，当前用户是内部员工。请基于以下系统边界，礼貌而简洁地拒绝用户的问题。

{BOUNDARY_RULES}

拒绝要求：用 1-2 句话说明"这是公司系统之外的信息/涉及隐私，无法提供"，语气客气专业。不要编造数据，不要说教。若用户的问题其实与公司系统相关且有工具可用，直接告诉用户如何提问。
"""


def refuse(question: str, model: str) -> str:
    """reject 分支：不进 ReAct，直接用小模型生成简短拒答。"""
    import llm
    msgs = [
        {"role": "system", "content": REJECT_PROMPT},
        {"role": "user", "content": f"用户问：{question}"},
    ]
    return llm.chat(model, msgs, temperature=0.5, max_tokens=200)


VERDICT_CN = {"simple": "简单→14b", "complex": "复杂→32b", "reject": "拒答→14b"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default="", help="只跑某题(子串)")
    ap.add_argument("--group", default="", help="只跑某组")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    questions = get_questions()
    if args.group:
        questions = [q for q in questions if q.group == args.group]
    if args.question:
        questions = [q for q in questions if args.question in q.question]
    if args.limit:
        questions = questions[: args.limit]

    gold = gold_map()
    report = []
    print(f"模型自选路由测试开始（三态），共 {len(questions)} 题\n" + "=" * 72)
    for i, q in enumerate(questions, 1):
        prompt = q.question
        print(f"\n[{i}/{len(questions)}] 组「{q.group}」· 题面标注 L{q.complexity}")
        print(f"  问: {prompt}")

        r = route(prompt, strategy="mixed")
        print(f"  【模型判定】{VERDICT_CN[r['verdict']]}  judge={r['judge_model']} ({r['judge_elapsed_s']}s)")
        print(f"  【判定理由】{r['reason']}")
        print(f"  【选路结果】模型 = {r['model']}, mode = {r['route_type']}")

        t0 = time.time()
        if r["route_type"] == "reject":
            ans = refuse(prompt, r["model"])
        else:
            ans = agent(prompt, model=r["model"], verbose=False)
        elapsed = time.time() - t0
        print(f"  【回答】{ans}")
        print(f"  【耗时】{elapsed:.0f}s")

        report.append({
            "question": prompt,
            "curated_complexity": q.complexity,
            "gold_verdict": gold.get(prompt),
            "judge_verdict": r["verdict"],
            "judge_reason": r["reason"],
            "chosen_model": r["model"],
            "route_type": r["route_type"],
            "answer": ans,
            "elapsed_s": round(elapsed, 1),
        })

    agree = sum(1 for r in report if r.get("gold_verdict") == r["judge_verdict"])
    total_gold = sum(1 for r in report if r.get("gold_verdict"))
    by_v = {}
    for v in ("simple", "complex", "reject"):
        sub = [r for r in report if r.get("gold_verdict") == v]
        if sub:
            ok = sum(1 for r in sub if r["judge_verdict"] == v)
            by_v[v] = f"{ok}/{len(sub)}"
    print("\n" + "=" * 72)
    print(f"完成 {len(report)} 题")
    print(f"模型判定 vs 黄金集一致率: {agree}/{total_gold}")
    print(f"  按类: simple {by_v.get('simple')}, complex {by_v.get('complex')}, reject {by_v.get('reject')}")
    with open(os.path.join(HERE, "report_routed.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("报告已存 agent/report_routed.json")


if __name__ == "__main__":
    main()