"""SQLite 持久化层测试：播种完整性 + 读 + 写事务 + 并发不超扣 + 持久化。"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_servers"))

_TMPDB = tempfile.mktemp(prefix="kb_store_", suffix=".db")
os.environ["APP_DB_FILE"] = _TMPDB

import store  # noqa: E402


def load_server(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class R:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name, ok, detail=""):
        self.passed += 1 if ok else 0
        self.failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {name} {detail}")


def main():
    t = R()
    ops = load_server("ops_store", ROOT / "mcp_servers/ops_server.py")
    store.init_db()

    # ---- 播种 ----
    t.check("播种-员工328", len(store._rows("SELECT 1 FROM employees")) == 328)
    t.check("播种-部门17", len(store.list_departments()) == 17)
    t.check("播种-客户30", len(store.list_customers()) == 30)
    t.check("播种-合同56", len(store._rows("SELECT 1 FROM contracts")) == 56)
    t.check("读-员工", (store.get_employee("陈志强") or {}).get("department") == "技术部")
    t.check("读-未知员工", store.get_employee("不存在的人") is None)

    # ---- 读经工具层 ----
    r = ops.lookup_employee("刘洋")
    t.check("工具-年假", r["found"] and r["leave_balance"] == 12, str(r.get("leave_balance")))
    r = ops.query_budget("技术部")
    t.check("工具-预算", r["found"] and r["annual_budget"] == 405)
    r = ops.get_customer_info("华宇科技")
    t.check("工具-客户RBAC拦截", r["access"] == "denied")

    # ---- 写事务：请假 ----
    before = store.get_employee("黄国栋")["leave_balance"]
    r = ops.create_leave_request("黄国栋", "2024-05-06", "2024-05-07")
    after = store.get_employee("黄国栋")["leave_balance"]
    t.check("写-请假成功", r["success"])
    t.check("写-余额扣减2", after == before - 2, f"{before}->{after}")
    t.check("写-落库leave_requests", len(store._rows("SELECT 1 FROM leave_requests")) >= 1)

    b2 = store.get_employee("张伟")["leave_balance"]
    r = ops.create_leave_request("张伟", "2024-05-06", "2024-05-20")
    t.check("写-超额拒绝", not r["success"])
    t.check("写-超额未扣减", store.get_employee("张伟")["leave_balance"] == b2)
    r = ops.create_leave_request("不存在的人", "2024-05-06", "2024-05-07")
    t.check("写-未知员工拒绝", not r["success"])

    # ---- 工单号原子自增 ----
    t1 = ops.create_ticket("刘洋", "线上服务异常", priority="紧急")
    t2 = ops.create_ticket("王强", "打印机故障")
    t.check("写-工单成功", t1["success"] and t2["success"])
    t.check("写-工单号唯一", t1["ticket_id"] != t2["ticket_id"], f"{t1['ticket_id']}/{t2['ticket_id']}")

    # ---- 并发不超扣：B 天余额，N 个线程各请 B 天，只应成功一次 ----
    _conn = sqlite3.connect(store.db_path())
    _conn.execute("UPDATE employees SET leave_balance=1 WHERE name='孙敏'")
    _conn.commit()
    _conn.close()
    results = []
    lock = threading.Lock()

    def worker():
        r = store.create_leave("孙敏", "2024-06-01", "2024-06-01", 1)
        with lock:
            results.append(r["ok"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    t.check("并发-仅一次成功", results.count(True) == 1, f"成功{results.count(True)}次/8")
    t.check("并发-余额不为负", store.get_employee("孙敏")["leave_balance"] == 0)

    # ---- 持久化：新连接仍可读到 ----
    t.check("持久化-重启可见", store.get_employee("黄国栋")["leave_balance"] == after)

    print(f"\n结果：{t.passed}/{t.passed + t.failed} 通过，{t.failed} 失败")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(_TMPDB + suffix)
        except OSError:
            pass
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()
