"""SQLite 持久化层：员工/部门/预算/客户/合同 + 请假/工单写入。

- 首次启动自动建表并从 data_generated.py 播种（幂等）；之后读改都走本库。
- 写操作事务化：请假扣减余额用 `BEGIN IMMEDIATE` + 条件 UPDATE，杜绝并发超扣；
  工单号由 AUTOINCREMENT 原子分配，不依赖进程内计数器。
- 路径由 `APP_DB_FILE` 指定（默认 `data/app.db`），每次连接读取，便于测试用临时库隔离。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

from data_generated import (
    BG_BUDGETS,
    BUDGETS,
    CONTRACTS,
    CUSTOMERS,
    DEPARTMENTS,
    EMPLOYEES,
)

from data_loader import DATA_DIR

_LOCK = threading.Lock()
_READY: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS employees(
    name TEXT PRIMARY KEY, department TEXT, position TEXT, level TEXT,
    leave_balance INTEGER, hire_date TEXT);
CREATE TABLE IF NOT EXISTS departments(
    name TEXT PRIMARY KEY, code TEXT, bg TEXT, manager TEXT, headcount INTEGER);
CREATE TABLE IF NOT EXISTS budgets(
    department TEXT PRIMARY KEY, annual REAL, spent REAL, remaining REAL, bg TEXT);
CREATE TABLE IF NOT EXISTS bg_budgets(
    name TEXT PRIMARY KEY, annual REAL, spent REAL, remaining REAL);
CREATE TABLE IF NOT EXISTS customers(
    name TEXT PRIMARY KEY, industry TEXT, contact TEXT, level TEXT,
    contract_amount REAL, credit TEXT);
CREATE TABLE IF NOT EXISTS contracts(
    contract_id TEXT PRIMARY KEY, customer TEXT, status TEXT, amount REAL,
    sign_date TEXT, owner TEXT);
CREATE TABLE IF NOT EXISTS leave_requests(
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee TEXT, start_date TEXT, end_date TEXT,
    days INTEGER, leave_type TEXT, reason TEXT, remaining REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS tickets(
    id INTEGER PRIMARY KEY AUTOINCREMENT, requester TEXT, title TEXT, description TEXT,
    priority TEXT, category TEXT, status TEXT, created_at TEXT);
"""


def db_path() -> str:
    return os.environ.get("APP_DB_FILE", os.path.join(DATA_DIR, "app.db"))


def _connect() -> sqlite3.Connection:
    path = db_path()
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(force: bool = False) -> None:
    """建表 + 播种（幂等）。进程内同一库只做一次；force=True 强制重播。"""
    path = db_path()
    with _LOCK:
        if path in _READY and not force:
            return
        conn = _connect()
        try:
            conn.executescript(_SCHEMA)
            n = conn.execute("SELECT COUNT(*) AS c FROM employees").fetchone()["c"]
            if n == 0 or force:
                _seed(conn, force=force)
            conn.commit()
        finally:
            conn.close()
        _READY.add(path)


def _seed(conn: sqlite3.Connection, force: bool = False) -> None:
    if force:
        for t in ("employees", "departments", "budgets", "bg_budgets", "customers", "contracts"):
            conn.execute(f"DELETE FROM {t}")
    conn.executemany(
        "INSERT OR REPLACE INTO employees VALUES(?,?,?,?,?,?)",
        [
            (n, e.get("department"), e.get("position"), e.get("level"),
             e.get("leave_balance"), e.get("hire_date"))
            for n, e in EMPLOYEES.items()
        ],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO departments VALUES(?,?,?,?,?)",
        [(d["name"], d.get("code"), d.get("bg"), d.get("manager"), d.get("headcount")) for d in DEPARTMENTS],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO budgets VALUES(?,?,?,?,?)",
        [(k, v.get("annual"), v.get("spent"), v.get("remaining"), v.get("bg")) for k, v in BUDGETS.items()],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO bg_budgets VALUES(?,?,?,?)",
        [(k, v.get("annual"), v.get("spent"), v.get("remaining")) for k, v in BG_BUDGETS.items()],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO customers VALUES(?,?,?,?,?,?)",
        [(k, v.get("industry"), v.get("contact"), v.get("level"), v.get("contract_amount"), v.get("credit"))
         for k, v in CUSTOMERS.items()],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO contracts VALUES(?,?,?,?,?,?)",
        [(k, v.get("customer"), v.get("status"), v.get("amount"), v.get("sign_date"), v.get("owner"))
         for k, v in CONTRACTS.items()],
    )


def _rows(sql: str, args: tuple = ()) -> list[dict]:
    init_db()
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


# ---- 读 ----

def get_employee(name: str) -> dict | None:
    rows = _rows("SELECT * FROM employees WHERE name=?", (name,))
    return rows[0] if rows else None


def list_departments() -> list[dict]:
    return _rows("SELECT * FROM departments ORDER BY rowid")


def get_budget(department: str) -> dict | None:
    rows = _rows("SELECT * FROM budgets WHERE department=?", (department,))
    return rows[0] if rows else None


def get_bg_budget(group: str) -> dict | None:
    rows = _rows("SELECT * FROM bg_budgets WHERE name=?", (group,))
    return rows[0] if rows else None


def get_customer(name: str) -> dict | None:
    rows = _rows("SELECT * FROM customers WHERE name=?", (name,))
    return rows[0] if rows else None


def list_customers(industry: str = "") -> list[dict]:
    if industry:
        return _rows("SELECT * FROM customers WHERE industry LIKE ? ORDER BY rowid", (f"%{industry}%",))
    return _rows("SELECT * FROM customers ORDER BY rowid")


def get_contract(contract_id: str) -> dict | None:
    rows = _rows("SELECT * FROM contracts WHERE contract_id=?", (contract_id,))
    return rows[0] if rows else None


def contracts_by_customer(customer: str) -> list[dict]:
    return _rows("SELECT * FROM contracts WHERE customer=? ORDER BY contract_id", (customer,))


def contracts_by_status(status: str) -> list[dict]:
    return _rows("SELECT * FROM contracts WHERE status LIKE ? ORDER BY contract_id", (f"%{status}%",))


# ---- 写（事务）----

def create_leave(name: str, start_date: str, end_date: str, days: int,
                 leave_type: str = "年假", reason: str = "") -> dict:
    """原子提交请假：余额校验 + 扣减 + 落库在同一事务内，失败整体回滚。"""
    init_db()
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT leave_balance FROM employees WHERE name=?", (name,)).fetchone()
        if row is None:
            conn.rollback()
            return {"ok": False, "reason": "not_found"}
        balance = row["leave_balance"] if row["leave_balance"] is not None else 0
        if days > balance:
            conn.rollback()
            return {"ok": False, "reason": "insufficient", "remaining": balance}
        remaining = balance - days
        conn.execute("UPDATE employees SET leave_balance=? WHERE name=?", (remaining, name))
        cur = conn.execute(
            "INSERT INTO leave_requests(employee,start_date,end_date,days,leave_type,reason,remaining,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (name, start_date, end_date, days, leave_type, reason, remaining,
             time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        conn.commit()
        return {"ok": True, "remaining": remaining, "id": cur.lastrowid}
    except sqlite3.Error:
        conn.rollback()
        return {"ok": False, "reason": "db_error"}
    finally:
        conn.close()


def create_ticket(requester: str, title: str, description: str = "",
                  priority: str = "普通", category: str = "通用") -> dict:
    """工单落库，编号由 AUTOINCREMENT 原子分配（TK-<id+1000>）。"""
    init_db()
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO tickets(requester,title,description,priority,category,status,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (requester, title, description, priority, category, "待处理",
             time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        conn.commit()
        tid = cur.lastrowid
        return {"ok": True, "seq": tid, "ticket_id": f"TK-{tid + 1000:04d}"}
    except sqlite3.Error:
        conn.rollback()
        return {"ok": False, "reason": "db_error"}
    finally:
        conn.close()
