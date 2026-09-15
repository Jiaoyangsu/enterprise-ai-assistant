"""线上质量监控：聚合 web/guard 反馈 + 人工兜底队列，输出三层指标。

三层（对应 工具可靠性 / 轨迹合理性 / 端到端任务完成）：
  1. 端到端质量   : PASS率、500率、低置信率、降级率、平均耗时、workflow 分布
  2. 人工兜底     : needs_human 已办/待办量（未完成任务的"人形成功线"）
  3. 轨迹质量     : 平均步数/工具数、重复调用率、白名单外调用率、工具报错率、Top 工具

用法:
  .venv/bin/python tools/monitor.py            # 全部历史
  .venv/bin/python tools/monitor.py --since 24 # 最近 24h

数据源(read-only): /tmp/web_feedback.jsonl, /tmp/guard_feedback.jsonl,
  /tmp/human_queue.jsonl, /tmp/human_answers.jsonl
（这些文件由 agent/web_app.py 在请求时实时写入；无数据则提示先产生会话。）

退出码: 0 正常；有 500/低置信异常时仍为 0（监控不阻塞发布，输出了好判断）。
"""
import argparse
import json
import os
import time
from collections import Counter

WEB_FEEDBACK = os.environ.get("WEB_FEEDBACK", "/tmp/web_feedback.jsonl")
GUARD_FEEDBACK = os.environ.get("GUARD_FEEDBACK", "/tmp/guard_feedback.jsonl")
HUMAN_QUEUE = os.environ.get("HUMAN_QUEUE", "/tmp/human_queue.jsonl")
HUMAN_ANSWERS = os.environ.get("HUMAN_ANSWERS", "/tmp/human_answers.jsonl")

# 与 agent/web_app.py queue_for_human 的低置信判定词保持同步
LOW_CONF_WORDS = (
    "未写明", "未提供", "未涉及", "未找到", "尚未明确", "不明确", "未公开",
    "请咨询", "咨询人力资源", "无法提供", "无法确认", "无权访问", "未在公开文档",
)


def read_jsonl(path: str) -> list:
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return out


def in_window(rec: dict, since: float) -> bool:
    return since == 0 or rec.get("ts", 0) >= since


def is_low_conf(ans: str, status: int) -> bool:
    ans = ans or ""
    return status == 500 or not ans or any(w in ans for w in LOW_CONF_WORDS)


def workflow_label(rec: dict) -> str:
    model = rec.get("model") or ""
    if model == "web/workflow-guide":
        return "引导"
    if model == "web/workflow-draft":
        return "草稿"
    if model == "web/workflow-inquiry":
        return "审批查询"
    if rec.get("workflow"):
        return "工作流"
    return "问答"


def trace_stats(recs: list) -> dict:
    """轨迹质量（只统计带 trace 字段的样本；无 trace 的旧数据跳过）。"""
    samples = [r for r in recs if isinstance(r.get("trace"), list) and r["trace"]]
    n = len(samples)
    if n == 0:
        return {"n": 0}
    steps = [len(r["trace"]) for r in samples]
    tools = [len({t["tool"] for t in r["trace"]}) for r in samples]
    repeat = 0          # 同题内相邻重复调用同一工具
    blocked = 0         # 白名单外/未知工具调用
    err = 0             # 工具返回 error 的步
    total_steps = sum(steps)
    tool_counter: Counter = Counter()
    for r in samples:
        prev = None
        for t in r["trace"]:
            name = t.get("tool") or ""
            obs = t.get("obs") or ""
            tool_counter[name] += 1
            if name == prev:
                repeat += 1
            prev = name
            if ("白名单" in obs and "拒绝" in obs) or ("未知工具" in obs):
                blocked += 1
            if '"error"' in obs or "error" in obs[:120]:
                err += 1
    return {
        "n": n,
        "steps_avg": round(sum(steps) / n, 2),
        "tools_distinct_avg": round(sum(tools) / n, 2),
        "repeat_step_rate": round(repeat / max(total_steps, 1), 4),
        "blocked_step_rate": round(blocked / max(total_steps, 1), 4),
        "tool_error_step_rate": round(err / max(total_steps, 1), 4),
        "top_tools": tool_counter.most_common(5),
    }


def main():
    ap = argparse.ArgumentParser(description="线上质量监控聚合器")
    ap.add_argument("--since", type=float, default=0, help="只看最近 N 小时（0=全部）")
    args = ap.parse_args()
    since = (time.time() - args.since * 3600) if args.since else 0

    web = [r for r in read_jsonl(WEB_FEEDBACK) if in_window(r, since)]
    guard = [r for r in read_jsonl(GUARD_FEEDBACK) if in_window(r, since)]
    queue = [r for r in read_jsonl(HUMAN_QUEUE) if in_window(r, since)]
    answers = [r for r in read_jsonl(HUMAN_ANSWERS) if in_window(r, since)]

    if not web and not guard:
        print("暂无会话数据。先启动 ./start_all.sh + agent/web_app.py 并产生一些问答后重跑。")
        return

    web_total = len(web)
    ok = sum(1 for r in web if r.get("status") == 200)
    err500 = sum(1 for r in web if r.get("status") == 500)
    degraded = sum(1 for r in web if r.get("status") == 503)
    low = sum(1 for r in web if is_low_conf(r.get("answer") or "", r.get("status") or 0))
    elapsed = [r["elapsed_s"] for r in web if r.get("elapsed_s")]
    wf = Counter(workflow_label(r) for r in web)
    users = Counter((r.get("user") or "?").split("·")[0] for r in web)

    # 人工兜底待办：queue 中 type=needs_human 且未被 answers 回填
    answered_keys = {str(a.get("qhash")) for a in answers}
    needs_human = [r for r in queue if r.get("type") == "needs_human"]
    pending = [r for r in needs_human if str(r.get("qhash")) not in answered_keys]
    guard_need = [r for r in guard if r.get("type") == "needs_human"]

    print("=" * 62)
    print("企业知识库助手 · 线上质量监控" + (f"（最近 {args.since:.0f}h）" if args.since else "（全部历史）"))
    print("=" * 62)
    print(f"\n[1] 端到端质量   会话={web_total}（guard_extra={len(guard)}）")
    print(f"    PASS/OK       {ok} ({ok / max(web_total, 1):.1%})")
    print(f"    500 异常       {err500} ({err500 / max(web_total, 1):.1%})")
    print(f"    降级 503       {degraded} ({degraded / max(web_total, 1):.1%})")
    print(f"    低置信/无法确认 {low} ({low / max(web_total, 1):.1%})   <- 未完成任务信号")
    print(f"    均耗时        {sum(elapsed) / max(len(elapsed), 1):.1f}s")
    print(f"    workflow      " + " | ".join(f"{k} {v}" for k, v in wf.most_common()))
    print(f"    活跃用户      " + " | ".join(f"{k}({v})" for k, v in users.most_common(8)))

    print(f"\n[2] 人工兜底    needs_human 已产生 {len(needs_human)} + guard {len(guard_need)}，"
          f"已回填 {len(answered_keys)}，待办 {len(pending)}")
    if pending:
        for p in pending[:5]:
            print(f"    · 待办: {(p.get('question') or '')[:52]}")

    ts = trace_stats(web)
    if ts["n"]:
        print(f"\n[3] 轨迹质量    带 trace 样本={ts['n']}")
        print(f"    平均步数       {ts['steps_avg']}")
        print(f"    平均不同工具   {ts['tools_distinct_avg']}")
        print(f"    重复调用步率   {ts['repeat_step_rate']:.2%}")
        print(f"    白名单外调用率 {ts['blocked_step_rate']:.2%}")
        print(f"    工具报错率     {ts['tool_error_step_rate']:.2%}")
        print("    Top工具        " + " | ".join(f"{k}({v})" for k, v in ts["top_tools"]))
    else:
        print("\n[3] 轨迹质量    暂无带 trace 样本（需在本次改动后于 web_app 产生会话）")


if __name__ == "__main__":
    main()