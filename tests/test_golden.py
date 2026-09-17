# -*- coding: utf-8 -*-
"""等价性 golden 双跑：自研 verifier.py 与 dsh guard/index.js 对同一语料必须判定一致。

用法：
  .venv/bin/python tests/test_golden.py            # 跑自研侧 + 若可用 guard 则双跑
"""
import sys, os, subprocess, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_servers"))

from verifier import verify
from golden_corpus import GOLDEN_CORPUS, trace_for

GUARD_PATH = os.path.expanduser("~/.dsh/profiles/web/node_modules/guard/index.js")


def python_result(entry) -> bool:
    r = verify(entry["question"] if "question" in entry else "q", trace_for(entry), entry["answer"])
    return r["verdict"] == "fail"


def guard_result(entry) -> bool:
    """调用 Node 侧 guard.test.verifyAnswer，返回是否拦截。"""
    script = r"""
const g = require(%s).test;
const cases = JSON.parse(process.argv[1]);
const out = [];
for (const c of cases) {
  const tail = [{ content: [{ type: 'tool-call', arguments: '{}', name: 't' },
                            { type: 'tool-result', content: [{ type: 'text', text: JSON.stringify(c.obs) }] }] }];
  const issues = g.verifyAnswer(tail, c.answer);
  out.push({ id: c.id, block: issues.length > 0 });
}
console.log(JSON.stringify(out));
""" % json.dumps(GUARD_PATH)
    proc = subprocess.run(
        ["node", "-e", script, json.dumps([{ "id": e["id"], "obs": e["trace_obs"], "answer": e["answer"] } for e in GOLDEN_CORPUS])],
        capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        print("guard 运行失败:", proc.stderr[:500])
        return {}
    return {r["id"]: r["block"] for r in json.loads(proc.stdout.strip())}


def main():
    py = {e["id"]: python_result(e) for e in GOLDEN_CORPUS}
    guard = guard_result(GOLDEN_CORPUS) if os.path.exists(GUARD_PATH) else {}

    print(f"{'id':<24} {'期望':<6} {'python':<7} {'guard':<6} {'一致'}")
    all_ok, diff = True, []
    for e in GOLDEN_CORPUS:
        want, p = e["expect_block"], py[e["id"]]
        gok = guard.get(e["id"], None)
        agree = (p == want) and (gok is None or gok == want)
        if not agree:
            all_ok = False
            diff.append(e["id"])
        print(f"{e['id']:<24} {str(want):<6} {str(p):<7} {str(gok):<6} {'✅' if agree else '❌ mismatch' if agree is False else '—'}")

    if guard:
        extra = set(py) ^ set(guard)
        if extra:
            print("guard/py id 不一致:", extra)
            all_ok = False

    print(f"\n结果：{'全部一致' if all_ok else ('差异: ' + ', '.join(diff))}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()