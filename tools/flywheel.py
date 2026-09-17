#!/usr/bin/env python3
"""数据飞轮：采集 -> 汇聚 -> 分类 -> 候选 -> 标注回灌 -> 落库 -> 回归（完整闭环）。

数据源（JSONL）：
  /tmp/guard_feedback.jsonl  guard 插件回馈（guarded/unanswered）— dsh 侧自动采集（唯一入口）
  /tmp/web_feedback.jsonl    自建前端 /api/chat 回馈（ok/error）
汇聚进 sqlite（tools/flywheel/feed.db），去重后按类别出候选清单 markdown。

人工标注 = 在 candidates.md 的 expected 列填答案（留空=忽略），保存后：
  1. promote  -> 写入评测集 benchmark.jsonl（幂等，全量保留）
  2. 落库     -> ingest_docs() 把答案写回知识库 data/documents.json（DOC-201 起，幂等）
  3. bench    -> 用评测集跑 react_agent，计算通过率
以上全流程由守护进程 tools/flywheel/daemon.py 自动完成（未标注的永不进评测）。

用法:
  python3 tools/flywheel.py ingest           # 摄入 jsonl -> db
  python3 tools/flywheel.py report           # 统计 + 候选清单 md（人工填 expected 列）
  python3 tools/flywheel.py promote          # 标注 -> 评测集 + 落库知识库
  python3 tools/flywheel.py ingest-docs      # 仅落库（不动评测集）
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
# 知识库（新知识落库目标）：data/documents.json，与 mcp_servers 共用单一数据源
DOC_JSON = os.environ.get("FLY_DOC_JSON",
                          os.path.join(os.path.dirname(HERE), "data", "documents.json"))
DOC_ID_PREFIX = 201  # 飞轮人工补充知识的 ID 段：DOC-201 起，避开内置 DOC-001~112

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
        "reason TEXT, created REAL DEFAULT 0)"
    )
    try:
        con.execute("ALTER TABLE cases ADD COLUMN reason TEXT")
    except sqlite3.OperationalError:
        pass
    try:  # 落库后回写知识库文档 ID（幂等标记：非空则不重复落库）
        con.execute("ALTER TABLE cases ADD COLUMN doc_id TEXT")
    except sqlite3.OperationalError:
        pass
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
        "> 流程：人工在 `expected` 处补期望内容(可留空=跳过)；`决策理由(reason)` 记录",
        "> 该答案凭什么定（制度X条/人工约定/待人工），歧义样本务必写明；确认后跑 `promote` 转评测题。",
        "",
        "| # | 来源 | 类别 | 问题 | 期望(expected) | 工具/拦截原因 | 次数 | 决策理由(reason) |",
        "|---|------|------|------|----------------|--------------|-----:|-----:|",
    ]
    prev_exp, prev_reason = _last_markup()  # 保留上一版人工标注，避免 re-report 丢失
    for idx, (sid, source, cat, question, answer, issues, _tools, seen) in enumerate(cand, 1):
        q = question.replace("|", "/")
        short_i = (issues or "")[:90].replace("|", "/")
        expected = prev_exp.get(question, "")
        reason = prev_reason.get(question, "")
        if not expected and cat == "human" and answer:
            expected = " ".join(answer.split())[:80]  # 人工已答即期望答案，promote 时自动录取
        lines.append(
            f"| {idx} | {source} | {CATEGORY.get(cat, cat)} | {q} | {expected} | {short_i} | {seen} | {reason} |"
        )
    md = "\n".join(lines) + "\n"
    with open(CAND_MD, "w") as f:
        f.write(md)
    print(f"候选清单已写入 {CAND_MD}（{len(cand)} 条，human 类别已自动预填 expected；可在表格末列补决策理由）")
    con.close()


def _last_markup() -> tuple[dict, dict]:
    """读上一版 candidates.md 中人工填写的期望与决策理由（question -> (expected, reason)）。"""
    if not os.path.exists(CAND_MD):
        return {}, {}
    expected, reason = {}, {}
    for line in open(CAND_MD, encoding="utf-8"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[3]:
            q = cells[3]
            exp = cells[4]
            if exp:
                expected[q] = exp
            if len(cells) >= 8 and cells[7]:
                reason[q] = cells[7]
    return expected, reason


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
        rows.append((question, expected, cells[1], cells[7] if len(cells) >= 8 else ""))
    if not rows:
        print("候选清单中没有已标注 expected 的样本。")
        return
    cases = []
    for i, (question, expected, md_source, reason) in enumerate(rows, 1):
        row = con.execute(
            "SELECT id, source, category FROM samples WHERE question=? ORDER BY id DESC LIMIT 1",
            (question,),
        ).fetchone()
        source = (row or (None, md_source, ""))[1]
        cat = (row or (None, "", ""))[2]
        t = time.time()
        con.execute(
            "INSERT OR IGNORE INTO cases(question, expected, source, category, reason, created) "
            "VALUES(?,?,?,?,?,?)",
            (question, expected, source, cat, reason, t),
        )
        # 已存在则补全 reason（历史正片常缺决策依据）
        con.execute(
            "UPDATE cases SET reason=? WHERE question=? AND reason IS NULL OR reason=''",
            (reason, question),
        )
        if row:
            con.execute("UPDATE samples SET promoted=1 WHERE id=?", (row[0],))
        print(f"[{i}] 评测题已收录: {question[:45]}  <- {CATEGORY.get(cat, md_source)}"
              + (f" | 理由: {reason[:30]}" if reason else " | 理由: (未填)"))
    con.commit()
    # 全量导出：benchmark.jsonl = cases 表全部评测题（新增 + 历史都保留，幂等）
    all_cases = con.execute(
        "SELECT question, expected, source, category, reason FROM cases "
        "ORDER BY id"
    ).fetchall()
    with open(BENCH_JSON, "w") as f:
        for c in all_cases:
            f.write(json.dumps({"question": c[0], "expected": c[1], "source": c[2],
                                "category": c[3], "reason": c[4]},
                               ensure_ascii=False) + "\n")
    with open(DOCS_MD, "w") as f:
        f.write("# 新知识落库审计（人工标注 → 自动写入 data/documents.json）\n\n")
        f.write("> 由飞轮 promote 自动落库，无需人工核对；此处仅作审计留痕。\n\n")
        for c in all_cases:
            if c[3] in ("human", "unanswered"):
                f.write(f"- 问题：{c[0]}\n")
                f.write(f"  回答(已落库)：{c[1]}\n")
                if c[4]:
                    f.write(f"  决策理由：{c[4]}\n")
    con.close()
    print(f"评测集已写入 {BENCH_JSON}（累计 {len(all_cases)} 条）；知识条目审计见 {DOCS_MD}")
    added = ingest_docs()
    if added:
        print(f"新知识已落库知识库：{added} 条 -> {DOC_JSON}")


# ===== 新知识落库：人工标注的答案写回知识库 data/documents.json（飞轮最后一环）=====
_DOC_STOP = set("吗呢吧啊呀的了着过和跟对把被是与否么怎么如何哪些哪个有没有会不会该")


def _extract_keywords(question: str, max_kw: int = 12) -> list:
    """从问题提取检索关键词（2~3 字中文 n-gram，过滤疑问/虚词），对齐 docs 检索打分。"""
    kws, seen = [], set()
    for n in (3, 2):
        for i in range(len(question) - n + 1):
            g = question[i:i + n]
            if all("\u4e00" <= c <= "\u9fff" for c in g) and not any(c in _DOC_STOP for c in g):
                if g not in seen:
                    seen.add(g)
                    kws.append(g)
            if len(kws) >= max_kw:
                return kws
    return kws


def _next_doc_id(docs: list) -> str:
    used = set()
    for d in docs:
        m = re.match(r"DOC-(\d+)$", str(d.get("id", "")))
        if m:
            used.add(int(m.group(1)))
    n = DOC_ID_PREFIX
    while n in used:
        n += 1
    return f"DOC-{n:03d}"


def _atomic_write_json(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def ingest_docs() -> int:
    """把 cases 表中人工已标注的答案落库到知识库，幂等（doc_id 非空则跳过）。返回新增条数。"""
    con = ensure_db()
    rows = con.execute(
        "SELECT id, question, expected FROM cases "
        "WHERE (doc_id IS NULL OR doc_id='') AND expected IS NOT NULL AND expected != '' "
        "ORDER BY id"
    ).fetchall()
    if not rows:
        con.close()
        return 0
    try:
        with open(DOC_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {"version": 1, "documents": []}
    docs = data.setdefault("documents", [])
    by_title = {str(d.get("title", "")).strip(): d.get("id", "") for d in docs}
    today = datetime.now().strftime("%Y-%m-%d")
    added = 0
    for cid, question, expected in rows:
        title = question.strip()
        if title in by_title:  # 已有同题文档：回填 doc_id 防重复，不重复落库
            con.execute("UPDATE cases SET doc_id=? WHERE id=?", (by_title[title], cid))
            continue
        doc_id = _next_doc_id(docs)
        docs.append({
            "id": doc_id,
            "title": title,
            "content": expected.strip(),
            "classification": "internal",
            "department": "全员",
            "keywords": _extract_keywords(question),
            "last_updated": today,
            "version": "v1.0",
        })
        by_title[title] = doc_id
        con.execute("UPDATE cases SET doc_id=? WHERE id=?", (doc_id, cid))
        added += 1
    if added:
        _atomic_write_json(DOC_JSON, data)
    con.commit()
    con.close()
    return added


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


def judge_llm(question: str, expected: str, ans: str) -> tuple[bool, str]:
    """LLM-as-a-Judge：确定性 judge 失败后的语义复核。

    若模型不可用（异常），返回 (False, reason="llm不可用") 并打印提示——调用方应保持原确定性结论。
    """
    sys.path.insert(0, os.path.join(HERE, "..", "agent"))
    try:
        from llm import chat_json
    except ModuleNotFoundError:
        return False, "agent依赖不可用"
    prompt = (
        "你是答案评审员。判断『待评审回答』与『标准答案』是否语义一致"
        "（允许不同措辞；关键点缺失或意思相反=不一致）。\n"
        "注意：标准答案若列明分档/多条要点（如数字档位、并列条款），待评审回答应覆盖主要要点，"
        "只答其中一部分、遗漏其他要点即视为不一致。\n"
        f"问题：{question}\n"
        f"标准答案：{expected}\n"
        f"待评审回答：{ans[:600]}\n"
        "只输出 JSON：{\"pass\": true/false, \"reason\": \"一句话理由\"}"
    )
    try:
        r = chat_json("qwen2.5:14b", [{"role": "user", "content": prompt}], temperature=0.0)
        ok = bool(r.get("pass"))
        return ok, ("llm判定" + ("通过" if ok else "不通过") + ": " + str(r.get("reason", ""))[:40])
    except Exception as e:
        print(f"  (judge_llm 不可用: {e})")
        return False, "llm不可用"


def bench(judge_with_llm: bool = False):
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
    relabelled = 0
    for i, c in enumerate(cases, 1):
        tr = []
        try:
            ans = agent(c["question"], trace=tr)
        except Exception as e:
            print(f"[{i}] {c['question'][:40]}  -> FAIL(异常 {e})")
            continue
        if os.environ.get("FLY_DEBUG"):
            for t in tr:
                print(f"    [debug] tool={t.get('tool')} args={str(t.get('args'))[:90]}")
                print(f"    [debug]   obs={str(t.get('obs'))[:160]}")
            print(f"    [debug] ans={ans[:200]!r}")
        ok, ev = judge(c["expected"], ans)
        tag = ""
        if not ok and judge_with_llm:
            ok2, ev2 = judge_llm(c["question"], c["expected"], ans)
            if ok2:
                relabelled += 1
                ok, ev, tag = True, "llm语义复核通过(" + ev2.split(":")[-1][:20] + ")", " [语义复核纠正]"
            elif "不可用" in ev2:
                print(f"  (bench 降级：LLM judge 不可用，{c['question'][:36]} 维持确定性 FAIL)")
            else:
                ev = "确定性" + ev + "；llm亦否(" + ev2.split(":")[-1][:20] + ")"
        ok_cnt += ok
        print(f"[{i}] {'PASS' if ok else 'FAIL'}{tag} {c['question'][:38]} | {ev}")
    print(f"== bench {ok_cnt}/{len(cases)}"
          + (f"（含 LLM 语义复核纠正 {relabelled} 条）" if judge_with_llm else "") + " ==")


def main():
    args = [a for a in sys.argv[1:]]
    judge_with_llm = False
    if "--judge" in args:
        i = args.index("--judge")
        if i + 1 < len(args) and args[i + 1] == "llm":
            judge_with_llm = True
        args = args[:i] + args[i + 2:]
    cmd = args[0] if args else "report"
    if cmd == "ingest":
        ingest()
    elif cmd == "promote":
        promote()
    elif cmd == "ingest-docs":
        n = ingest_docs()
        print(f"新知识落库: +{n} 条 -> {DOC_JSON}")
    elif cmd == "bench":
        bench(judge_with_llm=judge_with_llm)
    else:
        report()


if __name__ == "__main__":
    main()