"""前端页面回归：内嵌 JS 必须能通过语法检查。

背景：`agent/web_app.py` 的 PAGE/LOGIN_PAGE/HUMAN_PAGE 是 Python 三引号字符串，
在其中写 JS 的 `\n` 会被 Python 解释成真实换行，导致 JS 字符串字面量被截断
（SyntaxError），整段脚本无法解析 → 页面按钮点了没反应（"发不出去"）。
本测试把 Python 解释后的页面取出，交给 `node --check` 做语法校验。

node 缺失时跳过（打印提示，不判失败）。
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "mcp_servers"))

import web_app  # noqa: E402


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.notes = []

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        self.notes.append(f"{'✅' if ok else '❌'} {name} {detail}")


def _scripts(html: str) -> str:
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    return "\n;\n".join(blocks)


def main():
    t = TestResults()
    node = shutil.which("node")
    if not node:
        print("结果：0/0 通过，0 失败（未安装 node，跳过 JS 语法检查）")
        sys.exit(0)

    pages = {"PAGE": web_app.PAGE, "LOGIN_PAGE": web_app.LOGIN_PAGE, "HUMAN_PAGE": web_app.HUMAN_PAGE}
    for name, html in pages.items():
        js = _scripts(html)
        if not js.strip():
            t.check(f"{name}-含脚本", False, "未找到 <script> 块")
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(js)
            path = f.name
        r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        detail = "" if r.returncode == 0 else (r.stderr.strip().splitlines() or [""])[-1][:160]
        t.check(f"{name}-内嵌 JS 语法", r.returncode == 0, detail)

    print(f"\n结果：{t.passed}/{t.passed + t.failed} 通过，{t.failed} 失败")
    for n in t.notes:
        print(f"  {n}")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()
