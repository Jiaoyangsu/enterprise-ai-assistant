"""模型路由验证：用真实 LLM 判定复杂度并断言路由目标模型。

这是"用模型判断简单/复杂"的核心测试：
- 预期 simple 的题 → 路由到小模型(14b)
- 预期 complex 的题 → 路由到大模型(32b)
- judge 用的是 LLM（非规则），故此处验证的是模型判定结果

运行需：AutoDL Ollama 隧道已开（scripts/tunnel_ollama.sh）且模型已拉取。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
from router import route
from llm import MODEL_SMALL, MODEL_LARGE

# (问题, 期望复杂度, 说明)
CASES = [
    # ---- 简单：单点事实查询 ----
    ("技术部预算多少？", "simple", "单部门预算"),
    ("陈志强在哪个部门？", "simple", "单员工部门"),
    ("刘洋的剩余年假是多少？", "simple", "单员工年假"),
    ("华宇科技是做什么行业的？", "simple", "单客户行业"),
    ("我的手机号 13812345678 帮我脱敏", "simple", "单值脱敏"),
    # ---- 复杂：多步/对比/聚合/边界 ----
    ("先查技术部预算，再查产品部预算，哪个部门的剩余预算更多？", "complex", "跨部门比较"),
    ("技术部经理是谁？和运维部经理是同一个人吗？", "complex", "需要两次查询再比较"),
    ("如果没有登录身份，查客户信息会怎样？", "complex", "权限/边界兜底"),
    ("把这段个人信息脱敏，再检查是否有敏感词", "complex", "多步工具链"),
    ("徐磊是几几年入职的？职级和年假余额分别是多少？", "complex", "多字段聚合"),
]


def run():
    passed = failed = 0
    details = []
    for q, expected, note in CASES:
        # 给 judge 期望标签，避免模型"猜"我们的测试意图 -> 只传问题，不泄答案
        r = route(q)
        ok = (r["verdict"] == expected)
        model_ok = (MODEL_SMALL if expected == "simple" else MODEL_LARGE) == r["model"]
        if ok and model_ok:
            passed += 1
            details.append(f"  ✅ [{expected}] {q}\n      -> {r['model']}  ({r['reason']})")
        else:
            failed += 1
            details.append(
                f"  ❌ [{expected}] {q}\n      期望 {expected}，判定 {r['verdict']} -> {r['model']}\n      reason: {r['reason']}"
            )
    print(f"模型路由测试：{passed}/{len(CASES)} 通过，{failed} 失败")
    print("明细：")
    for d in details:
        print(d)
    return failed


if __name__ == "__main__":
    sys.exit(1 if run() else 0)