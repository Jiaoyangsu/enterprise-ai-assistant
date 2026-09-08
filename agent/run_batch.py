"""批量运行 7 组全部 36 题，用 qwen2.5:14b ReAct 循环，展示每题推理与最终回复。

用法: .venv/bin/python agent/run_batch.py [--group 组名] [--include-refuse]
"""
import argparse
import sys
import os
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

from test_questions import get_questions  # noqa: E402
from agent.react_agent import agent  # noqa: E402


def build_prompt(q) -> str:
    """把身份标记转成自然语言上下文附加在问题前。"""
    question = q.question
    prefix = []
    if "##用户身份##" in question:
        prefix.append("[当前用户：刘洋，登录状态]")
        question = question.replace("##用户身份##", "").strip()
    if "##身份:技术部##" in question:
        prefix.append("[请求方身份：技术部员工]")
        question = question.replace("##身份:技术部##", "").strip()
    # 第一人称问题（"我"）默认当前用户 = 刘洋（与测试集的默认身份一致）
    if "我" in question or "我的" in question:
        prefix.append("[当前用户：刘洋]")
    resp = " ".join(prefix)
    return (resp + " " + question).strip() if resp else question


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="", help="只跑某个组")
    ap.add_argument("--question", default="", help="只跑某个问题(子串)")
    ap.add_argument("--limit", type=int, default=0, help="最多跑 N 题")
    args = ap.parse_args()

    questions = get_questions()
    if args.group:
        questions = [q for q in questions if q.group == args.group]
    if args.question:
        questions = [q for q in questions if args.question in q.question]
    if args.limit:
        questions = questions[: args.limit]

    total = len(questions)
    passed = failed = 0
    report = []

    print(f"开始 ReAct 批量测试（模型 qwen2.5:14b），共 {total} 题\n" + "=" * 70)
    for i, q in enumerate(questions, 1):
        prompt = build_prompt(q)
        print(f"\n[{i}/{total}] 组「{q.group}」 L{q.complexity} 复杂度题")
        print(f"  问: {prompt}")
        t0 = time.time()
        ans = agent(prompt, verbose=True)
        el = time.time() - t0
        print(f"  答: {ans}")
        print(f"  用时: {el:.0f}s")
        report.append({
            "group": q.group, "complexity": q.complexity,
            "question": prompt, "answer": ans,
            "expected_tool": q.expected_tool, "elapsed_s": round(el, 1),
        })

    # 汇总
    print("\n" + "=" * 70)
    print(f"完成 {total} 题")
    with open("agent/report_batch.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("报告已存: agent/report_batch.json")


if __name__ == "__main__":
    main()