#!/bin/bash
# 全量回归（本地/CI 通用）：任一用例失败即非零退出。
# 用法：./tools/run_all_tests.sh   （可用 PY=python 覆盖解释器）
set -u
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY=python3

fail=0
run() {
  local name=$1; shift
  local rc=0 line
  local out; out=$("$@" 2>&1) || rc=$?
  line=$(printf '%s\n' "$out" | grep -E "结果：|全部一致" | tail -1)
  if [ "$rc" -ne 0 ]; then
    echo "❌ $name  rc=$rc  ${line:-}"
    printf '%s\n' "$out" | tail -15
    fail=1
  else
    echo "✅ $name  ${line:-通过}"
  fi
}

for t in test_verifier run_suite test_baseline test_context test_entities test_store \
         test_retrieval test_oauth test_audit test_public_app test_web_page test_import \
         test_golden; do
  run "$t" "$PY" "tests/$t.py"
done

if command -v node >/dev/null 2>&1; then
  run "guard(repo)" node profiles/guard/test.js
  live="$HOME/.dsh/profiles/web/node_modules/guard/test.js"
  if [ -f "$live" ]; then run "guard(dsh)"; fi
else
  echo "⚠️  node 不可用，跳过 guard 测试"
fi

echo
if [ "$fail" -eq 0 ]; then echo "全部通过 ✅"; else echo "存在失败 ❌"; fi
exit "$fail"