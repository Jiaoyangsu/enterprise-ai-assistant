"""真实数据接入适配器测试：dry-run 不写盘、真实导入写入 app.db 与 documents.json、
--wipe 清手残留、缺必需列报错。"""
import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = os.environ.get("PY") or str(ROOT / ".venv" / "bin" / "python")
if not os.path.exists(PY):
    PY = sys.executable

IMPORTER = str(ROOT / "tools" / "import_customer_data.py")


def _write_csv(dirp: Path, name: str, header: list[str], rows: list[list]):
    with open(dirp / name, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


class R:
    def __init__(self):
        self.passed = self.failed = 0

    def check(self, name, ok, detail=""):
        self.passed += 1 if ok else 0
        self.failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {name} {detail}")


def _db_people(db: str) -> set:
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM employees")}
    finally:
        conn.close()


def main():
    try:
        import sqlite3  # noqa: F401
    except ImportError:
        print("无 sqlite3，跳过")
        return 0
    r = R()
    src = Path(tempfile.mkdtemp(prefix="imp_src_"))
    dst = Path(tempfile.mkdtemp(prefix="imp_dst_"))
    _write_csv(src, "employees.csv",
               ["name", "department", "position", "level", "leave_balance", "hire_date"],
               [["张三", "技术中心", "工程师", "主管", 10, "2021-04-01"],
                ["李四", "技术中心", "经理", "经理", "bad", "2020-01-01"]])  # reconcile 跳过李四
    _write_csv(src, "departments.csv",
               ["name", "code", "bg", "manager", "headcount"],
               [["技术中心", "T001", "技术", "张三", 42]])
    _write_csv(src, "documents.csv",
               ["id", "title", "content", "keywords", "classification", "department", "version"],
               [["", "停车规定", "园区停车需登记", "停车;园区", "制度库", "行政部", "1.0"],
                ["", "考勤办法", "考勤以系统为准", "考勤", "制度库", "人事部", "1.0"]])

    # dry-run：不写盘
    cp = subprocess.run([PY, IMPORTER, "--data-dir", str(src), "--db", str(dst / "app.db"),
                         "--dry-run"],
                        capture_output=True, text=True)
    out_ok = "dry-run" in cp.stdout and "employees: 1 行" in cp.stdout
    r.check("dry-run-输出正确", cp.returncode == 0 and out_ok, cp.stdout.splitlines()[1] if len(cp.stdout.splitlines()) > 1 else "")
    r.check("dry-run-不写app.db", not (dst / "app.db").exists())
    r.check("dry-run-不写documents.json", not (src / "documents.json").exists())

    # 真实导入（不 wipe，仅新库）
    cp = subprocess.run([PY, IMPORTER, "--data-dir", str(src), "--db", str(dst / "app.db")],
                        capture_output=True, text=True)
    r.check("导入-成功退出", cp.returncode == 0, cp.stdout.strip().splitlines()[-2] if cp.returncode != 0 else "")
    r.check("导入-员工落库且非法行跳过", _db_people(str(dst / "app.db")) == {"张三"},
            f"actual={_db_people(str(dst / 'app.db'))}")
    docs = (src / "documents.json")
    r.check("导入-文档生成并自动编号", docs.exists() and len(__import__("json").loads(docs.read_text("utf-8"))["documents"]) == 2)

    # 追加一轮：加一篇 + 覆盖 id=DOC-201
    _write_csv(src, "documents.csv",
               ["id", "title", "content", "keywords", "classification", "department", "version"],
               [["DOC-201", "停车规定v2", "园区停车需先登记再入场", "停车;园区", "制度库", "行政部", "2.0"]])
    cp = subprocess.run([PY, IMPORTER, "--data-dir", str(src), "--db", str(dst / "app.db")],
                        capture_output=True, text=True)
    docs2 = __import__("json").loads((src / "documents.json").read_text("utf-8"))["documents"]
    r.check("导入-按id覆盖", any(d["id"] == "DOC-201" and d["version"] == "2.0" and "停车规定v2" in d["title"] for d in docs2))
    r.check("导入-不重复追加既有id", sum(1 for d in docs2 if d["id"] == "DOC-201") == 1)

    # 缺必需列 → 非零退出
    bad = Path(tempfile.mkdtemp(prefix="imp_bad_"))
    _write_csv(bad, "customers.csv", ["name", "industry"], [["缺列", "x"]])
    cp = subprocess.run([PY, IMPORTER, "--data-dir", str(bad), "--db", str(bad / "app.db")],
                        capture_output=True, text=True)
    r.check("缺列-非零退出且提示", cp.returncode != 0 and "缺必需列" in cp.stdout)

    # --wipe 清手残留
    _write_csv(src, "employees.csv",
               ["name", "department", "position", "level", "leave_balance", "hire_date"],
               [["王六", "市场部", "专员", "初级", 8, "2023-05-01"], ["张三", "技术中心", "工程师", "主管", 10, "2021-04-01"]])
    cp = subprocess.run([PY, IMPORTER, "--data-dir", str(src), "--db", str(dst / "app.db"), "--wipe"],
                        capture_output=True, text=True)
    r.check("wipe-替换而非残留", _db_people(str(dst / "app.db")) == {"张三", "王六"},
            f"actual={_db_people(str(dst / 'app.db'))}")

    print(f"\n结果：{r.passed}/{r.passed + r.failed} 通过，{r.failed} 失败")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())