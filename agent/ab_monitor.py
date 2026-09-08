"""AB 实验后台监控：每 INTERVAL 秒记一行进度到 ab_progress.log，AB 结束后汇总退出。"""
import os
import sys
import time
import subprocess
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_LOG = os.path.join(HERE, "ab_run.log")
PROG_LOG = os.path.join(HERE, "ab_progress.log")
INTERVAL = 300


def count_done():
    try:
        with open(RUN_LOG, encoding="utf-8") as f:
            lines = f.readlines()
        done = sum(1 for l in lines if ")" in l and ("(OK" in l or "(XX" in l))
        mode = ""
        for l in reversed(lines):
            if "mode=" in l:
                mode = l.strip().split("mode=")[1].split()[0] if "mode=" in l else ""
                break
        cur = ""
        for l in reversed(lines):
            if "/36]" in l:
                cur = l.strip()
                break
        return done, mode, cur
    except Exception:
        return None, "", ""


def main():
    while True:
        proc = subprocess.run(["pgrep", "-f", "ab_experiment.py"], capture_output=True)
        alive = proc.returncode == 0
        done, mode, cur = count_done()
        line = (f"[{datetime.now().strftime('%H:%M:%S')}] AB={'RUNNING' if alive else 'DONE'} "
                f"当前模式={mode} 已完成={done if done is not None else '?'}/108 "
                f"当前题={cur}")
        with open(PROG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line, flush=True)
        if not alive:
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()