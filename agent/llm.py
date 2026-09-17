"""LLM 客户端封装：走本机 SSH 隧道后到 AutoDL Ollama 的 OpenAI 兼容端点。

单一职责：把 prompt -> 模型 -> 文本回复 的最小闭环做出来，供 agent 路由复用。
不用外部依赖，用 urllib 直连（避免装 openai 包），支持 temperature/max_tokens 与
非流式 JSON 结构化输出（可选 json_mode）。

高可用：单调用带重试 + 模型降级链——主模型失败自动切备用模型，全程超时保护。
"""
import json
import urllib.request
import urllib.error
import os
import time

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
MODEL_SMALL = os.environ.get("MODEL_SMALL", "qwen2.5:14b")
MODEL_LARGE = os.environ.get("MODEL_LARGE", "qwen2.5:32b")
# 降级链：主模型 -> 备用模型（可用环境变量覆盖顺序）
FALLBACK_MODELS = [m for m in os.environ.get(
    "MODEL_FALLBACKS", f"{MODEL_SMALL},{MODEL_LARGE}"
).split(",") if m.strip()]

DEFAULT_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "45"))       # 单次请求超时
DEFAULT_RETRIES = int(os.environ.get("LLM_RETRIES", "2"))        # 每模型重试次数
RETRY_BACKOFF = float(os.environ.get("LLM_BACKOFF", "1.5"))       # 重试间隔(秒)
# 对齐 dsh settings.yaml contextWindow=32768（旧值 16384 导致长文档截断）
LLM_NUM_CTX = int(os.environ.get("LLM_NUM_CTX", "32768"))
# 可选备用 Ollama 端点（主 11434 不可用时自动切换到 11435，用逗号分隔）
LLM_FALLBACK_URLS = [u.strip() for u in os.environ.get(
    "LLM_FALLBACK_URLS", ""
).split(",") if u.strip()]


def _post(model: str, messages: list, temperature: float, max_tokens: int,
          json_mode: bool, timeout: int, base_url: str = BASE_URL) -> str:
    """单次 HTTP 请求。失败抛 RuntimeError（带可读信息）。"""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "options": {"num_ctx": LLM_NUM_CTX},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
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


def chat(
    model: str,
    messages: list,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    json_mode: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    fallbacks: list[str] | None = None,
) -> str:
    """调用 chat/completions，返回 assistant 文本。

    健壮性：
    - 每个模型尝试 retries+1 次，重试间隔指数退避（backoff 逐次 +1s）。
    - 主模型彻底失败后，依次尝试 fallbacks 里的备用模型（默认 MODEL_FALLBACKS）。
    - 全部失败抛 RuntimeError（带已尝试历史），由调用方兜底（agent 有回退回答）。
    """
    chain = [model] + [m for m in (fallbacks or FALLBACK_MODELS) if m != model]
    urls = [BASE_URL] + [u for u in LLM_FALLBACK_URLS if u != BASE_URL]
    errs: list[str] = []
    last = None
    for url in urls:
      for m in chain:
        for attempt in range(retries + 1):
          try:
            return _post(m, messages, temperature, max_tokens, json_mode, timeout, base_url=url)
          except RuntimeError as e:
            last = e
            errs.append(f"{url}:{m}[第{attempt+1}次]: {e}")
            if attempt < retries:
              time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise RuntimeError("; ".join(errs[-3:]) if errs else str(last))


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