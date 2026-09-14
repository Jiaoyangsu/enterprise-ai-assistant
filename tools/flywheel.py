#!/usr/bin/env python3
"""数据飞轮：采集 -> 汇聚 -> 分类 -> 候选(评测回灌原料)。

数据源（JSONL）：
  /tmp/guard_feedback.jsonl  guard 插件回馈（guarded/unanswered）
  /tmp/web_feedback.jsonl    自建前端 /api/chat 回馈（ok/error）
汇聚进 sqlite（tools/flywheel/feed.db），去重后按类别出候选清单 markdown，
候选经人工标注 expected 后可回灌评测集（见 report 输出指引）。

用法:
  python3 tools/flywheel.py ingest   # 摄入 jsonl -> db
  python3 tools/flywheel.py report   # 统计 + 候选清单 md
"""
from __future__ import annotations

import json
import os
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
    con.commit()
    return con


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
    for idx, (sid, source, cat, question, _answer, issues, _tools, seen) in enumerate(cand, 1):
        short_q = question.replace("|", "/")[:60]
        short_i = (issues or "")[:90].replace("|", "/")
        lines.append(
            f"| {idx} | {source} | {CATEGORY.get(cat, cat)} | {short_q} |  | {short_i} | {seen} |"
        )
    md = "\n".join(lines) + "\n"
    with open(CAND_MD, "w") as f:
        f.write(md)
    print(f"候选清单已写入 {CAND_MD}（{len(cand)} 条，可人工标注 expected）")
    con.close()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "ingest":
        ingest()
    else:
        report()


if __name__ == "__main__":
    main()