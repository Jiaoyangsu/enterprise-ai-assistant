"""LLM/知识服务健康守护：探活 + 隧道自愈 + 单点故障报告。

用于缓解 AutoDL 实例/隧道单点：实例在但隧道断时可自动重建；
实例不在时给出明确告警（需人工开机）。可在 web_app 预检前调用。

用法:
  python3 tools/llm_health.py check   # 单次输出状态（json）
  python3 tools/llm_health.py watch   # 常驻：每 20s 探活，隧道断则自愈
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

LOG = "/tmp/llm_health.log"

TUNNEL_EXP = "/tmp/opencode_tunnel_v3.exp"

OLLAMA_HOST = os.environ.get("OLLAMA_HEALTH_HOST", "127.0.0.1:11434")

TUNNEL_PORT = int(os.environ.get("TUNNEL_PORT", "11435"))

INSTANCE_HOST = os.environ.get("INSTANCE_HOST", "connect.cqa1.seetacloud.com")
INSTANCE_PORT = int(os.environ.get("INSTANCE_PORT", "41036"))

MCP_PORTS = [
    ("docs", 8001),
    ("ops", 8002),
    ("security", 8003),
]

MODEL = "qwen2.5:14b"

HOST, PORT_STR = OLLAMA_HOST.rsplit(":", 1)
PORT = int(PORT_STR)


def tcp_check(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ollama_tags_has_model(host: str | None = None) -> bool:
    base = host or f"http://{OLLAMA_HOST}"
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3.0) as r:
            data = json.loads(r.read().decode("utf-8"))
        return any(m.get("name", "").startswith(MODEL) for m in data.get("models", []))
    except Exception:
        return False


def probe() -> dict:
    local_llm = tcp_check(HOST, PORT)
    tunnel = tcp_check(HOST, TUNNEL_PORT)
    state = {
        "local_ollama_11434": local_llm,
        "ollama_model": local_llm and ollama_tags_has_model(),
        "tunnel_11435": tunnel,
        "remote_ollama": tunnel and ollama_tags_has_model(host=f"http://{HOST}:{TUNNEL_PORT}"),
        "instance_reachable": tcp_check(INSTANCE_HOST, INSTANCE_PORT, timeout=8.0),
        "mcp": {},
    }
    for name, port in MCP_PORTS:
        state["mcp"][name] = tcp_check("127.0.0.1", port)
    state["ok"] = bool(state["ollama_model"]) and all(state["mcp"].values())
    state["remote_ok"] = bool(state["remote_ollama"])
    return state


def log(line: str):
    with open(LOG, "a") as f:
        f.write(time.strftime("[%H:%M:%S] ") + line + "\n")


def rebuild_tunnel() -> bool:
    if tcp_check(HOST, TUNNEL_PORT):
        return True
    for pat in ("opencode_tunnel_v3", "ssh -p 41036"):
        subprocess.run(["pkill", "-f", pat], capture_output=True)
    try:
        with open(os.devnull, "w") as dn:
            subprocess.Popen(["expect", TUNNEL_EXP], stdout=dn, stderr=dn,
                             start_new_session=True)
    except OSError as e:
        log(f"rebuild_tunnel spawn failed: {e}")
        return False
    log("tunnel rebuild spawned, waiting…")
    time.sleep(8)
    return tcp_check(HOST, TUNNEL_PORT)


def watch_loop(interval: int = 20):
    log("health watcher started")
    while True:
        s = probe()
        log(f"probe: local={s['local_ollama_11434']}/model={s['ollama_model']} "
            f"tunnel={s['tunnel_11435']} remote={s['remote_ollama']} "
            f"instance={s['instance_reachable']} mcp={s['mcp']} ok={s['ok']}")
        if not s["tunnel_11435"] and s["instance_reachable"]:
            if rebuild_tunnel():
                log("tunnel healed")
            else:
                log("tunnel rebuild FAILED")
        elif not s["ollama_model"]:
            log("WARN: local ollama missing/模型未加载")
        for _ in range(interval):
            time.sleep(1)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "watch":
        watch_loop()
    else:
        print(json.dumps(probe(), ensure_ascii=False))


if __name__ == "__main__":
    main()