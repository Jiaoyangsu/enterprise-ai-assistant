#!/usr/bin/env python3
"""数据飞轮守护进程：采集 → 汇聚 → 候选 → 标注发现 → 回灌评测 → 落库知识库 → 回归，全自动。

工作原理（每 poll_interval 秒一轮）：
  1. ingest    —— 把 dsh guard 的 /tmp/guard_feedback.jsonl 与自研 /tmp/web_feedback.jsonl
                  增量导入 feed.db（去重 + 分类），source 是 dsh 侧，无需改动 guard。
  2. report    —— 生成/更新 candidates.md 候选清单（人工标注前保持 expected 空白 = 忽略）。
  3. promote   —— 检测 candidates.md 中人工新填的 expected 列（或 human 类别自动回填），
                  幂等 (INSERT OR IGNORE) 写入评测集 benchmark.jsonl + cases 表。
  4. 落库      —— promote 内自动 ingest_docs()：把新知识写回 data/documents.json，
                  docs_server 按 mtime 热重载即时生效（飞轮最后一环闭合）。
  5. bench     —— benchmark.jsonl 有变化时，用评测集对自研 react_agent 跑回归，刷新通过率。

未标注的候选永远停留在 candidates.md（人工选择忽略），绝不自动进评测集。

用法:
  .venv/bin/python tools/flywheel/daemon.py            # 前台循环
  nohup .venv/bin/python tools/flywheel/daemon.py --judge llm > /tmp/flywheel_daemon.log 2>&1 &   # 后台常驻
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import flywheel as fw  # noqa: E402

POLL_INTERVAL = float(os.environ.get("FLY_POLL", "60"))
BENCH_MARKER = os.path.join(fw.FLY_DIR, ".bench_last_hash")


def _mtime(path: str) -> tuple[str, int] | None:
    if not os.path.exists(path):
        return None
    try:
        return hashlib.md5(open(path, "rb").read()).hexdigest(), int(os.path.getmtime(path))
    except OSError:
        return None


def _bench_hash() -> str | None:
    return _mtime(fw.BENCH_JSON)[0] if os.path.exists(fw.BENCH_JSON) else None


def _last_run() -> str:
    try:
        return open(BENCH_MARKER).read().strip()
    except OSError:
        return ""


def auto_promote() -> int:
    """从 candidates.md 提炼人工新标注 → 评测集。返回本次新增条数。"""
    promote_before = _count_cases()
    fw.promote()
    promote_after = _count_cases()
    return promote_after - promote_before


def _count_cases() -> int:
    con = fw.ensure_db()
    try:
        return con.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    finally:
        con.close()


def auto_bench(judge_with_llm: bool = False) -> bool:
    """只有评测集 hash 变化时才跑 bench，避免每轮重复。返回是否执行。"""
    cur = _bench_hash()
    if cur is None:
        return False
    last = _last_run()
    if last == cur:
        return False
    print(f"[daemon] 评测集变更，跑回归 bench...")
    try:
        fw.bench(judge_with_llm=judge_with_llm)
    except Exception as e:
        print(f"[daemon] bench 失败: {e}")
    with open(BENCH_MARKER, "w") as f:
        f.write(cur)
    return True


def one_round(judge_with_llm: bool = False):
    started = time.time()
    try:
        fw.ingest()
    except Exception as e:
        print(f"[daemon] ingest 异常: {e}")
    try:
        fw.report()
    except Exception as e:
        print(f"[daemon] report 异常: {e}")
    added = auto_promote()
    if added:
        print(f"[daemon] promote 新增 {added} 条评测题")
    auto_bench(judge_with_llm=judge_with_llm)
    fw.ensure_db().close()
    print(f"[daemon] round done in {time.time() - started:.1f}s (next in {POLL_INTERVAL:.0f}s)")


def main():
    args = [a for a in sys.argv[1:]]
    judge_with_llm = False
    if "--judge" in args:
        i = args.index("--judge")
        if i + 1 < len(args) and args[i + 1] == "llm":
            judge_with_llm = True
        args = args[:i] + args[i + 2:]
    # 首次启动立即落一轮入库（把积压的 feedback 全部汇聚），再进入间歇轮询
    print(f"[daemon] 数据飞轮守护进程启动，轮询间隔 {POLL_INTERVAL:.0f}s"
          + ("（LLM 语义复核开启）" if judge_with_llm else ""))
    one_round(judge_with_llm=judge_with_llm)
    while True:
        time.sleep(POLL_INTERVAL)
        one_round(judge_with_llm=judge_with_llm)


if __name__ == "__main__":
    main()