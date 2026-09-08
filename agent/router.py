"""模型路由（AB 结论：36 题上混合架构无收益，全 14b 最优）。

这版只做"策略选择"，保留两种可用策略：
- "14b"  （默认，生产使用）：所有问题一律 qwen2.5:14b，不调用 judge，最快最稳。
         AB 实测 14b 35/36 (97%), 2.6min；混合 32/36, 11.3min；全 32b 31/36, 15.9min。
- "mixed"（保留实验用，生产弃用）：先 14b 裁判三态判定(simple/complex/reject)，
         再按 verdict 路由 14b/32b/拒答。

verdict -> 目标（仅 mixed）：
- simple  -> MODEL_SMALL (14b)
- complex -> MODEL_LARGE (32b)
- reject  -> MODEL_SMALL + route_type="reject"（直接拒答）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from llm import MODEL_SMALL, MODEL_LARGE  # noqa: E402


def route(question: str, strategy: str = "14b",
          model_small: str | None = None, model_large: str | None = None):
    """返回 {model, verdict, reason, route_type, ...}。
    strategy: "14b"(默认，不做复杂度判定) 或 "mixed"(先 judge 再路由，实验用)。"""
    small = model_small or MODEL_SMALL
    large = model_large or MODEL_LARGE
    if strategy == "14b":
        return {
            "model": small,
            "verdict": "14b",
            "reason": "默认策略：全量 14b（AB 实测最优），不启用复杂度判定",
            "route_type": "react",
            "judge_model": None,
            "judge_elapsed_s": 0.0,
            "strategy": "14b",
        }
    # --- 以下为 mixed 策略（实验保留，生产不使用） ---
    from complexity_judge import judge  # noqa: F401
    t0 = time.time()
    verdict = judge(question)
    elapsed = time.time() - t0
    if verdict["verdict"] == "complex":
        model, route_type = large, "react"
    elif verdict["verdict"] == "reject":
        model, route_type = small, "reject"
    else:
        model, route_type = small, "react"
    return {
        "model": model,
        "verdict": verdict["verdict"],
        "reason": verdict["reason"],
        "route_type": route_type,
        "judge_model": MODEL_SMALL,
        "judge_elapsed_s": round(elapsed, 2),
        "strategy": "mixed",
    }


if __name__ == "__main__":
    for q in [
        "技术部预算多少？",
        "先查技术部预算，再查产品部预算，哪个部门的剩余预算更多？",
        "同事的工资是多少？",
    ]:
        r = route(q)
        print(f"[default 14b] {r['model']} | {q}")
    for q in ["技术部预算多少？", "同事的工资是多少？"]:
        r = route(q, strategy="mixed")
        print(f"[mixed] {r['verdict']} | {r['model']} | {q} | {r['reason']}")