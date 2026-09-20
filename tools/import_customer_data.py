"""真实数据接入适配器：把客户侧 CSV 源导入 SQLite（app.db）与知识库（documents.json）。

目的：上线「换真实数据」从改代码变成跑一次导入。CSV 表头与 store.py 建表字段同名
（见 samples/data_templates/*.csv，可直接套用模板）：

  employees   name,department,position,level,leave_balance,hire_date
  departments name,code,bg,manager,headcount
  budgets     department,annual,spent,remaining,bg
  customers   name,industry,contact,level,contract_amount,credit
  contracts   contract_id,customer,status,amount,sign_date,owner
  documents   id?,title,content,keywords?,classification?,department?,version?

语义：
- 业务表按主键 upsert（INSERT OR REPLACE）；主键：employees/departments/customers=name、
  budgets=department、contracts=contract_id。
- documents.csv 按 id 覆盖（给出 id 则沿用；缺省自动分配 DOC-<max+1>），与现有
  documents.json 合并落盘（保留未被导入的既有文档）。
- 数据全部留在 data/（或 --data-dir），不联网、不入库凭证。
- --dry-run 只计算不写入。

示例：
  cp -r samples/data_templates ./import
  # 编辑 ./import/*.csv 为真实数据（注意编码 UTF-8）
  .venv/bin/python tools/import_customer_data.py --data-dir ./import  # 实际导入到 data/
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP = os.path.join(_ROOT, "mcp_servers")
if _MCP not in sys.path:
    sys.path.insert(0, _MCP)

from store import _SCHEMA  # noqa: E402  <-- 复用建表语句，避免与持久化层漂移

# 字段名（与 store.py 建表一致）→ 数值列
TABLES: dict[str, list[str]] = {
    "employees": ["name", "department", "position", "level", "leave_balance", "hire_date"],
    "departments": ["name", "code", "bg", "manager", "headcount"],
    "budgets": ["department", "annual", "spent", "remaining", "bg"],
    "customers": ["name", "industry", "contact", "level", "contract_amount", "credit"],
    "contracts": ["contract_id", "customer", "status", "amount", "sign_date", "owner"],
}
NUMERIC = {"leave_balance", "headcount", "annual", "spent", "remaining", "contract_amount", "amount"}
KEYS = {  # upsert 主键
    "employees": "name", "departments": "name", "budgets": "department",
    "customers": "name", "contracts": "contract_id",
}

DOC_CSV_FIELDS = ["id", "title", "content", "keywords", "classification", "department", "version"]


def _coerce(row: dict, table: str) -> dict | None:
    """数值列转 int/float；空串→None；非法数字返回 None 表示该行应跳过。"""
    out: dict = {}
    for col in TABLES[table]:
        v = row.get(col, "")
        if v is None or str(v) == "":
            out[col] = None
            continue
        if col in NUMERIC:
            try:
                out[col] = float(v) if ("." in str(v)) else int(v)
            except ValueError:
                return None
        else:
            out[col] = str(v)
    return out


def _connect(db: str):
    import sqlite3
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def import_tables(db: str, dir_path: str, tables: dict[str, str], wipe: bool = False) -> dict[str, int]:
    """按内容导入业务表，返回每表成功行数。wipe=True 先清空业务表（换真实数据）。"""
    if not os.path.exists(dir_path):
        return {}
    counts: dict[str, int] = {}
    conn = _connect(db)
    try:
        if wipe:
            for table in ("employees", "departments", "budgets", "bg_budgets", "customers", "contracts"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        for table, fname in tables.items():
            path = os.path.join(dir_path, fname)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8-sig") as f:
                rd = csv.DictReader(f)
                if rd.fieldnames is None:
                    print(f"  ⚠️ {fname}: 空文件，跳过"); continue
                missing = [c for c in TABLES[table] if c not in rd.fieldnames]
                if missing:
                    print(f"  ❌ {fname}: 缺必需列 {missing}，整表跳过")
                    raise SystemExit(2)
                rows, bad = [], 0
                for r in rd:
                    c = _coerce(r, table)
                    if c is None:
                        bad += 1
                        continue
                    rows.append(c)
            if rows:
                key = KEYS[table]
                cols = ", ".join(TABLES[table])
                ph = ", ".join("?" for _ in TABLES[table])
                sql = f"INSERT OR REPLACE INTO {table}({cols}) VALUES({ph})"
                conn.executemany(sql, [tuple(r[c] for c in TABLES[table]) for r in rows])
                conn.commit()
            counts[table] = len(rows)
            print(f"  {table}: {len(rows)} 行" + (f"（跳过格式非法 {bad} 行）" if bad else ""))
    finally:
        conn.close()
    return counts


def _next_doc_id(docs: list[dict]) -> str:
    max_n = 100
    for d in docs:
        m = re.fullmatch(r"[A-Za-z]+-(\d+)", str(d.get("id", "")))
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"DOC-{max_n + 1:03d}"


def import_documents(dst_docs_path: str, src_path: str, prefix: str) -> tuple[int, int]:
    """合并 documents.csv → documents.json。返回 (新增, 更新)。"""
    if not os.path.isfile(src_path):
        return 0, 0
    exists = os.path.isfile(dst_docs_path)
    docs: list[dict] = json.load(open(dst_docs_path, encoding="utf-8")).get("documents", []) \
        if exists else []
    doc_version = json.load(open(dst_docs_path, encoding="utf-8")).get("version", 1) \
        if exists else 1
    by_id = {d.get("id"): d for d in docs if d.get("id")}
    added = updated = 0
    with open(src_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            required = ["title", "content"]
            if any(not str(r.get(c, "")).strip() for c in required):
                continue
            rid = (r.get("id") or "").strip() or _next_doc_id(docs)
            rec = {
                "id": rid,
                "title": (r.get("title") or "").strip(),
                "content": r.get("content") or "",
                "keywords": [k for k in (r.get("keywords") or "").replace("，", ",").split(",") if k.strip()]
                or "",
                "classification": (r.get("classification") or "").strip(),
                "department": (r.get("department") or "").strip(),
                "version": (r.get("version") or "1.0").strip(),
                "last_updated": __import__("time").strftime("%Y-%m-%d"),
            }
            if rid in by_id:
                by_id[rid].update(rec)
                updated += 1
            else:
                by_id[rid] = rec
                docs.append(rec)
                added += 1
    payload = {"version": doc_version, "documents": docs}
    with open(dst_docs_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return added, updated


def main() -> int:
    ap = argparse.ArgumentParser(description="真实数据接入：CSV → SQLite + documents.json")
    ap.add_argument("--data-dir", default=os.path.join(_ROOT, "data"), help="data 目录（文档/db 写入处）")
    ap.add_argument("--db", default=None, help="app.db 路径（缺省 <data-dir>/app.db）")
    for t in TABLES:
        ap.add_argument(f"--{t}", default=None, help=f"{t}.csv 路径（缺省 <data-dir>/{t}.csv）")
    ap.add_argument("--documents", default=None, help="documents.csv 路径（缺省 <data-dir>/documents.csv）")
    ap.add_argument("--id-prefix", default="DOC-", help="新文档 id 前缀（缺省 DOC-）")
    ap.add_argument("--wipe", action="store_true",
                    help="先清空业务表再导入（换真实数据时用，避免残留合成数据）")
    ap.add_argument("--dry-run", action="store_true", help="只计算不写入")
    args = ap.parse_args()

    db = args.db or os.path.join(os.path.abspath(args.data_dir), "app.db")
    tmp = tempfile.mkdtemp(prefix="import_dryrun_") if args.dry_run else None
    if args.dry_run:
        db = os.path.join(tmp, "app.db")

    tables = {t: os.path.join(args.data_dir, f"{t}.csv") for t in TABLES}
    for t in TABLES:
        if getattr(args, t):
            tables[t] = getattr(args, t)

    print("== 业务表导入 ==")
    counts = import_tables(db, args.data_dir, tables, wipe=args.wipe)
    if args.dry_run and os.path.exists(db):
        os.remove(db)

    print("== 文档导入 ==")
    docs_csv = args.documents or os.path.join(args.data_dir, "documents.csv")
    dst_docs = os.path.join(os.path.abspath(args.data_dir), "documents.json")
    if args.dry_run:
        dst_docs = os.path.join(tmp, "documents.json")
    if os.path.isfile(docs_csv):
        added, updated = import_documents(dst_docs, docs_csv, args.id_prefix)
        print(f"  documents: 新增 {added}，更新 {updated}"
              + ("" if args.dry_run else f"，落在 {dst_docs}"))
    else:
        print("  (未提供 documents.csv，跳过)")

    if args.dry_run:
        print("完成（dry-run，未写盘）")
    else:
        print("完成（已写入 " + db + "）" + ("；wipe：已先清空合成数据" if args.wipe else ""))
    if counts:
        print("  业务表行数:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())