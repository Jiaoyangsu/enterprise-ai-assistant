"""LLM 客户端封装：走本机 SSH 隧道后到 AutoDL Ollama 的 OpenAI 兼容端点。

单一职责：把 prompt -> 模型 -> 文本回复 的最小闭环做出来，供 agent 路由复用。
不用外部依赖，用 urllib 直连（避免装 openai 包），支持 temperature/max_tokens 与
非流式 JSON 结构化输出（可选 json_mode）。
"""
import json
import urllib.request
import urllib.error
import os

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
MODEL_SMALL = os.environ.get("MODEL_SMALL", "qwen2.5:14b")
MODEL_LARGE = os.environ.get("MODEL_LARGE", "qwen2.5:32b")


def chat(
    model: str,
    messages: list,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    json_mode: bool = False,
    timeout: int = 180,
) -> str:
    """调用 OpenAI 兼容 chat/completions，返回 assistant 文本。"""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM HTTP {e.code}: {e.read().decode('utf-8')[:300]}") from e
    except Exception as e:
        raise RuntimeError(f"LLM call failed: {e}") from e
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected LLM response: {body}") from e


def chat_json(model: str, messages: list, temperature: float = 0.0, max_tokens: int = 1024) -> dict:
    """调用并解析 JSON 结构化输出。失败时抛错，由调用方兜底。"""
    text = chat(model, messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise ValueError(f"model returned non-JSON: {text[:200]}")