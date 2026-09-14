#!/usr/bin/env python3
"""数据飞轮：采集 -> 汇聚 -> 分类 -> 候选(评测回灌原料)。

数据源（JSONL）：
  /tmp/guard_feedback.jsonl  guard 插件回馈（guarded/unanswered）
  /tmp/web_feedback.jsonl    自建前端 /api/chat 回馈（ok/error）
汇聚进 sqlite（tools/flywheel/feed.db），去重后按类别出候选清单 markdown，
候选经人工标注 expected 后可回灌评测集（见 report 输出指引）。

用法:
  python3 tools/flywheel.py ingest           # 摄入 jsonl -> db
  python3 tools/flywheel.py report           # 统计 + 候选清单 md（人工填 expected 列）
  python3 tools/flywheel.py promote          # 把已标 expected 的候选 -> 评测集(print cases+benchmark.jsonl)
  python3 tools/flywheel.py bench            # 用评测集跑 react_agent，计算通过率
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
FLY_DIR = os.path.join(HERE, "flywheel")
DB = os.path.join(FLY_DIR, "feed.db")
CAND_MD = os.path.join(FLY_DIR, "candidates.md")

SOURCES = {
    "guard": os.environ.get("GUARD_FEEDBACK", "/tmp/guard_feedback.jsonl"),
    "web": os.environ.get("WEB_FEEDBACK", "/tmp/web_feedback.jsonl"),
}

UNKNOWN_MARK = re_words = (r"未写明|未提供|未涉及|未找到|尚未明确|未查到|不明确|没有明确")

CATEGORY = {
    "guarded": "被回检拦截(质量差)",
    "unanswered": '答"未写明"类(覆盖盲区)',
    "error": "请求失败(异常)",
    "human": "人工已回填(可直接转评测种子)",
    "ok": "正常放行",
}


def ensure_db():
    os.makedirs(FLY_DIR, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS samples("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "qhash TEXT, ts REAL, source TEXT, question TEXT,"
        "answer TEXT, issues TEXT, tools TEXT, category TEXT,"
        "seen INTEGER DEFAULT 1, promoted INTEGER DEFAULT 0)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS cases("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "question TEXT UNIQUE, expected TEXT, source TEXT, category TEXT,"
        "created REAL DEFAULT 0)"
    )
    con.commit()
    return con


BENCH_JSON = os.path.join(FLY_DIR, "benchmark.jsonl")
DOCS_MD = os.path.join(FLY_DIR, "promote_docs.md")


def classify(source: str, rec: dict) -> str:
    if source == "human":
        return "human"
    if source == "guard":
        t = rec.get("type")
        if t == "guarded":
            return "guarded"
        if t == "unanswered":
            return "unanswered"
        return "ok"
    if source == "web":
        if rec.get("source") == "human":
            return "human"
        if rec.get("status") == 500:
            return "error"
        ans = rec.get("answer", "")
        if ans and __import__("re").search(UNKNOWN_MARK, ans):
            return "unanswered"
        return "ok"
    return "ok"


def ingest():
    con = ensure_db()
    n_new = 0
    for source, path in SOURCES.items():
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                question = (rec.get("question") or "").strip()
                if not question:
                    continue
                qhash = json.dumps({"q": question, "s": source}, ensure_ascii=False)
                cat = classify(source, rec)
                answer = rec.get("finalText") or rec.get("answer") or ""
                issues = json.dumps(rec.get("issues", []), ensure_ascii=False)
                tools = json.dumps(rec.get("tools", []), ensure_ascii=False)
                cur = con.execute(
                    "SELECT id, seen FROM samples WHERE qhash=?", (qhash,)
                )
                row = cur.fetchone()
                if row:
                    con.execute(
                        "UPDATE samples SET seen=seen+1, ts=?, answer=?, issues=?, category=? WHERE id=?",
                        (rec.get("ts", time.time()), answer[:2000], issues, cat, row[0]),
                    )
                else:
                    con.execute(
                        "INSERT INTO samples(qhash,ts,source,question,answer,issues,tools,category)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (qhash, rec.get("ts", time.time()), source, question,
                         answer[:2000], issues, tools, cat),
                    )
                    n_new += 1
    con.commit()
    con.close()
    print(f"ingest done: +{n_new} new samples")


def report():
    import re
    con = ensure_db()
    total = con.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
    by_cat = dict(
        con.execute("SELECT category, COUNT(*) FROM samples GROUP BY category").fetchall()
    )
    print("== 样本统计 ==")
    print(f"total: {total}")
    for c, n in sorted(by_cat.items()):
        print(f"  {CATEGORY.get(c, c)}: {n}")

    cand = con.execute(
        "SELECT id,source,category,question,answer,issues,tools,seen FROM samples "
        "WHERE category IN ('guarded','unanswered','error','human') AND promoted=0 "
        "ORDER BY seen DESC, id DESC LIMIT 60"
    ).fetchall()
    if not cand:
        print("无未处理候选。")
        return
    lines = [
        "# 数据飞轮候选清单（人工标注后回灌评测）",
        "",
        "> 流程：人工在 `expected` 处补期望内容(可留空=跳过)；确认后跑 `promote` 转评测题。",
        "",
        "| # | 来源 | 类别 | 问题 | 期望(expected) | 工具/拦截原因 | 次数 |",
        "|---|------|------|------|----------------|--------------|-----:|",
    ]
    prev_exp = _last_expected()  # 保留上一版人工标注，避免 re-report 丢失
    for idx, (sid, source, cat, question, answer, issues, _tools, seen) in enumerate(cand, 1):
        q = question.replace("|", "/")
        short_i = (issues or "")[:90].replace("|", "/")
        expected = prev_exp.get(question, "")
        if not expected and cat == "human" and answer:
            expected = " ".join(answer.split())[:80]  # 人工已答即期望答案，promote 时自动录取
        lines.append(
            f"| {idx} | {source} | {CATEGORY.get(cat, cat)} | {q} | {expected} | {short_i} | {seen} |"
        )
    md = "\n".join(lines) + "\n"
    with open(CAND_MD, "w") as f:
        f.write(md)
    print(f"候选清单已写入 {CAND_MD}（{len(cand)} 条，human 类别已自动预填 expected）")
    con.close()


def _last_expected() -> dict:
    """读上一版 candidates.md 中人工填写的 expected（question->expected）。"""
    if not os.path.exists(CAND_MD):
        return {}
    out = {}
    for line in open(CAND_MD, encoding="utf-8"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[3]:
            out[cells[3]] = cells[4] if cells[4] else ""
    return out


def promote():
    con = ensure_db()
    rows = []
    for line in open(CAND_MD, encoding="utf-8"):
        if "|---" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].strip().isdigit():
            continue
        question = cells[3]
        expected = cells[4]
        if not expected or expected.isspace():
            continue
        rows.append((question, expected, cells[1]))
    if not rows:
        print("候选清单中没有已标注 expected 的样本。")
        return
    cases = []
    for i, (question, expected, md_source) in enumerate(rows, 1):
        row = con.execute(
            "SELECT id, source, category FROM samples WHERE question=? ORDER BY id DESC LIMIT 1",
            (question,),
        ).fetchone()
        source = (row or (None, md_source, ""))[1]
        cat = (row or (None, "", ""))[2]
        t = time.time()
        con.execute(
            "INSERT OR IGNORE INTO cases(question, expected, source, category, created) VALUES(?,?,?,?,?)",
            (question, expected, source, cat, t),
        )
        if row:
            con.execute("UPDATE samples SET promoted=1 WHERE id=?", (row[0],))
        print(f"[{i}] 评测题已收录: {question[:45]}  <- {CATEGORY.get(cat, md_source)}")
        cases.append({"question": question, "expected": expected, "source": source, "category": cat})
    con.commit()
    with open(BENCH_JSON, "w") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with open(DOCS_MD, "w") as f:
        f.write("# 新知识条目草案（人工核对后落库到 documents_extra）\n\n")
        for c in cases:
            if c["category"] in ("human", "unanswered"):
                f.write(f"- 问题：{c['question']}\n  回答(待核对)：{c['expected']}\n")
    con.close()
    print(f"评测集已写入 {BENCH_JSON}（{len(cases)} 条）；知识条目标注见 {DOCS_MD}")


def judge(expected: str, ans: str) -> tuple[bool, str]:
    clauses = [c for c in re.split(r"[。！？；;，,\n]", expected) if len(c) >= 6]
    num_clauses = [c for c in clauses if re.search(r"\d", c)]
    if num_clauses:
        ok = all(any(d in ans for d in re.findall(r"\d+(?:\.\d+)?", c)) for c in num_clauses)
        evidence = "数字断言:" + "/".join(re.findall(r"\d+(?:\.\d+)?", "".join(num_clauses)))
    elif clauses:
        ok = any(c in ans for c in clauses)
        evidence = "句片命中:" + clauses[0][:30]
    else:
        ok = expected in ans
        evidence = "全文包含"
    return ok, evidence


def bench():
    if not os.path.exists(BENCH_JSON):
        print("尚无评测集，先跑 promote。")
        return
    sys.path.insert(0, os.path.join(HERE, "..", "agent"))
    try:
        from react_agent import agent
    except ModuleNotFoundError:
        print("bench 需要项目依赖：请用 .venv/bin/python tools/flywheel.py bench")
        return
    cases = [json.loads(l) for l in open(BENCH_JSON, encoding="utf-8")]
    ok_cnt = 0
    for i, c in enumerate(cases, 1):
        try:
            ans = agent(c["question"])
        except Exception as e:
            print(f"[{i}] {c['question'][:40]}  -> FAIL(异常 {e})")
            continue
        ok, ev = judge(c["expected"], ans)
        ok_cnt += ok
        print(f"[{i}] {'PASS' if ok else 'FAIL'} {c['question'][:38]} | {ev}")
    print(f"== bench {ok_cnt}/{len(cases)} ==")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "ingest":
        ingest()
    elif cmd == "promote":
        promote()
    elif cmd == "bench":
        bench()
    else:
        report()


if __name__ == "__main__":
    main()