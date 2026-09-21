"""Trace 瀑布图：把一次 ReAct 轨迹渲染成逐步 span 与耗时归因。

输入两种：
  - 实跑：直接给一个问题，走 agent() 拿到带 ts/llm_dur/tool_dur 的 trace 渲染。
  - 回放：读 /tmp/web_feedback.jsonl 里带耗时字段的 trace（自研侧 deploy 之后才有）。

输出每个 span 的墙钟偏移 + LLM/工具耗时条，以及本轮总耗时拆分为
LLM / TOOL / 其余(消息拼装·JSON·守护开销) 三块，一眼定位瓶颈在哪一步。

用法:
  .venv/bin/python tools/trace_waterfall.py "公司年假制度怎么规定的"
  .venv/bin/python tools/trace_waterfall.py --replay 3
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp_servers"))

WEB_FEEDBACK = os.environ.get("WEB_FEEDBACK", "/tmp/web_feedback.jsonl")
_SCALE = 40


def _dur(e: dict, key: str):
    v = e.get(key)
    return round(float(v), 2) if isinstance(v, (int, float)) else None


def render(title: str, trace: list, wall: float, extra: str = "") -> None:
    """trace: [{tool|step,args,obs,thought,ts,llm_dur,tool_dur}, ...]（answer 步 tool 为空）。"""
    print(f"\n### {title}   {extra}")
    if not trace:
        print("  (无 trace)")
        return

    t0 = trace[0].get("ts")
    t0 = t0 if isinstance(t0, (int, float)) else 0.0
    spans = []
    for e in trace:
        ld = _dur(e, "llm_dur")
        td = _dur(e, "tool_dur")
        span = (ld or 0.0) + (td or 0.0)
        spans.append((e, ld, td, span))

    max_span = max((s for _, _, _, s in spans), default=0.0) or 0.001
    scale = _SCALE / max_span

    def bar(v):
        return "#" * max(0, int(round((v or 0.0) * scale)))

    llm_all = sum(ld or 0.0 for _, ld, _, _ in spans)
    tool_all = sum(td or 0.0 for _, _, td, _ in spans)
    other = max(0.0, wall - llm_all - tool_all)
    pct = lambda v: f"{v / wall * 100:.0f}%" if wall else "-"

    print(f"  总耗时 {wall:.1f}s = LLM {llm_all:.1f}s({pct(llm_all)}) "
          f"+ TOOL {tool_all:.1f}s({pct(tool_all)}) + 其余 {other:.1f}s({pct(other)})")

    for i, (e, ld, td, _) in enumerate(spans):
        ts = e.get("ts")
        off = (round(ts - t0, 2) if isinstance(ts, (int, float)) else None)
        if e.get("step") == "answer":
            name = f"answer[{e.get('kind')}]"
        else:
            name = (e.get("tool") or "?")[:20]
        row = (f"  t+{off:>6.2f}s " if off is not None else "  t+       ")
        row += f"LLM {ld if ld is not None else '-':>6}  TOOL {td if td is not None else '-':>5}  "
        row += f"#{i}:{name:<22} {bar(ld)}"
        if td:
            row += f" @{bar(td)}"
        print(row)
        if i < len(spans) - 1:
            nxt_ts = spans[i + 1][0].get("ts")
            if isinstance(ts, (int, float)) and isinstance(nxt_ts, (int, float)):
                gap = max(0.0, nxt_ts - ts - (ld or 0.0) - (td or 0.0))
                if gap > 0.05:
                    print(f"  {'':>11} GAP   +{gap:.2f}s (JSON 解析/回检/消息拼装)")


def run_live(question: str) -> None:
    import react_agent
    trace = []
    t0 = time.time()
    try:
        ans = react_agent.agent(question, trace=trace, allow_retry=True)
        ok = "√"
    except Exception as ex:  # noqa: BLE001
        ans = f"<异常> {ex}"
        ok = "×"
    wall = time.time() - t0
    render(question, trace, wall, f"result={ok}")
    print(f"  答案: {str(ans)[:80]}")


def replay(n: int) -> None:
    rows = []
    if os.path.exists(WEB_FEEDBACK):
        with open(WEB_FEEDBACK, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = r.get("trace")
                if (isinstance(t, list) and t and
                        all(isinstance(e, dict) and (e.get("ts") is not None or e.get("llm_dur") is not None)
                            for e in t)):
                    rows.append(r)
    if not rows:
        print(f"暂无可用耗时 trace（{WEB_FEEDBACK} 中需要含 ts/llm_dur 的新格式记录）。")
        return
    rows = rows[-n:]
    for r in rows:
        q = (r.get("question") or "?")[:40]
        wall = r.get("elapsed_s") or 0.0
        render(q, r["trace"], float(wall),
               f"model={r.get('model') or ''}".rstrip())


def main() -> int:
    ap = argparse.ArgumentParser(description="Trace 瀑布图：逐步 span + 耗时归因")
    ap.add_argument("question", nargs="?", help="实跑一个问题的文字")
    ap.add_argument("--replay", type=int, default=0,
                    help="回放 web_feedback.jsonl 最后 N 条带耗时字段的 trace")
    args = ap.parse_args()
    if args.replay:
        replay(args.replay)
        return 0
    if args.question:
        run_live(args.question)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())