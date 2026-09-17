"""语义检索层：本地 embedding + 分块 + 向量召回（供 docs_server 混合检索）。

- 后端：Ollama 的 OpenAI 兼容接口 `/v1/embeddings`（默认 `bge-m3`），完全本地、不出网。
- 分块：按句子边界把每篇文档切成 ~`CHUNK_CHARS` 字的块（带一句重叠），块级向量。
- 缓存：`data/embeddings_cache.json` 按 `sha256(model + text)` 缓存向量，文档变更才重算。
- 降级：embedding 后端不可用时 `semantic_search` 返回空，docs_server 自动回退纯关键词检索。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.request

from data_loader import DATA_DIR

EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
EMBED_BASE_URL = os.environ.get("EMBED_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
EMBED_TIMEOUT = float(os.environ.get("EMBED_TIMEOUT", "20"))
EMBED_CACHE_FILE = os.environ.get(
    "EMBED_CACHE_FILE", os.path.join(DATA_DIR, "embeddings_cache.json")
)
SEMANTIC_ENABLED = os.environ.get("SEMANTIC_ENABLED", "1") not in ("0", "false", "False")

CHUNK_CHARS = int(os.environ.get("EMBED_CHUNK_CHARS", "420"))
MIN_COSINE = float(os.environ.get("EMBED_MIN_COSINE", "0.35"))

_LOCK = threading.Lock()
_CACHE: dict = {}
_CACHE_DIRTY = False
_INDEX = {"sig": None, "chunks": [], "vectors": []}
_HEALTH = {"ok": None, "checked": 0.0}


def _hash(text: str) -> str:
    return hashlib.sha256(f"{EMBED_MODEL}\n{text}".encode("utf-8")).hexdigest()


def _load_cache() -> None:
    global _CACHE
    try:
        with open(EMBED_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _CACHE = data
    except (OSError, json.JSONDecodeError):
        _CACHE = {}


def _save_cache() -> None:
    global _CACHE_DIRTY
    if not _CACHE_DIRTY:
        return
    try:
        tmp = EMBED_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f)
        os.replace(tmp, EMBED_CACHE_FILE)
        _CACHE_DIRTY = False
    except OSError:
        pass


def _embed_remote(texts: list[str]) -> list[list[float]] | None:
    """调用 Ollama /v1/embeddings；失败返回 None（调用方降级）。"""
    body = json.dumps({"model": EMBED_MODEL, "input": texts}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{EMBED_BASE_URL}/v1/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        rows = sorted(payload.get("data") or [], key=lambda r: r.get("index", 0))
        vecs = [r.get("embedding") for r in rows]
        if len(vecs) != len(texts) or any(not isinstance(v, list) or not v for v in vecs):
            return None
        return vecs
    except Exception:
        return None


def available() -> bool:
    """embedding 后端是否可用（结果缓存 300s，避免每次检索都探活）。"""
    if not SEMANTIC_ENABLED:
        return False
    now = time.time()
    if _HEALTH["ok"] is not None and now - _HEALTH["checked"] < 300:
        return bool(_HEALTH["ok"])
    ok = _embed_remote(["ping"]) is not None
    _HEALTH.update(ok=ok, checked=now)
    return ok


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """批量取向量（带缓存）；任一文本失败则整体返回 None（保证索引完整）。"""
    if not texts:
        return []
    with _LOCK:
        if not _CACHE:
            _load_cache()
        keys = [_hash(t) for t in texts]
        missing = [(k, t) for k, t in zip(keys, texts) if k not in _CACHE]
        if missing:
            vecs = _embed_remote([t for _, t in missing])
            if vecs is None:
                return None
            for (k, _), v in zip(missing, vecs):
                _CACHE[k] = v
            global _CACHE_DIRTY
            _CACHE_DIRTY = True
            _save_cache()
        return [_CACHE[k] for k in keys]


def _split_sentences(content: str) -> list[str]:
    parts = re.split(r"(?<=[。；！？])", content)
    return [p for p in parts if p.strip()]


def chunk_docs(docs: list[dict]) -> list[dict]:
    """把文档切成块（带一句重叠），保留 doc_id/title/密级/部门元数据。"""
    chunks: list[dict] = []
    for doc in docs:
        sents = _split_sentences(doc.get("content", ""))
        buf: list[str] = []
        size = 0
        prev_tail = ""
        for s in sents:
            buf.append(s)
            size += len(s)
            if size >= CHUNK_CHARS:
                text = (prev_tail + "".join(buf)).strip()
                chunks.append(_chunk_meta(doc, text, len(chunks)))
                prev_tail = "".join(buf[-1:])
                buf, size = [], len(prev_tail)
        if buf:
            text = (prev_tail + "".join(buf)).strip()
            if text:
                chunks.append(_chunk_meta(doc, text, len(chunks)))
    return chunks


def _chunk_meta(doc: dict, text: str, idx: int) -> dict:
    return {
        "doc_id": doc["id"],
        "title": doc["title"],
        "classification": doc["classification"],
        "department": doc.get("department", ""),
        "text": text,
        "chunk_idx": idx,
    }


def _docs_signature(docs: list[dict]) -> str:
    h = hashlib.sha256()
    for d in docs:
        h.update(d["id"].encode("utf-8"))
        h.update(d.get("content", "").encode("utf-8"))
    return h.hexdigest()


def _ensure_index(docs: list[dict]) -> bool:
    """构建/复用块向量索引；embedding 不可用返回 False。"""
    sig = _docs_signature(docs)
    if _INDEX["sig"] == sig and _INDEX["vectors"]:
        return True
    chunks = chunk_docs(docs)
    vecs = embed_texts([c["text"] for c in chunks])
    if vecs is None:
        return False
    _INDEX.update(sig=sig, chunks=chunks, vectors=vecs)
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def semantic_search(query: str, docs: list[dict], top_k: int = 8) -> dict:
    """返回 {doc_id: {"score": 最高块余弦, "chunk": 命中块文本}}；不可用返回 {}。"""
    if not query or not SEMANTIC_ENABLED or not available():
        return {}
    try:
        if not _ensure_index(docs):
            return {}
        qv = embed_texts([query])
        if not qv:
            return {}
        qvec = qv[0]
        best: dict[str, dict] = {}
        for chunk, vec in zip(_INDEX["chunks"], _INDEX["vectors"]):
            cos = _cosine(qvec, vec)
            if cos < MIN_COSINE:
                continue
            cur = best.get(chunk["doc_id"])
            if cur is None or cos > cur["score"]:
                best[chunk["doc_id"]] = {"score": cos, "chunk": chunk["text"]}
        ranked = sorted(best.items(), key=lambda kv: kv[1]["score"], reverse=True)[:top_k]
        return dict(ranked)
    except Exception:
        return {}


def stats() -> dict:
    return {
        "model": EMBED_MODEL,
        "enabled": SEMANTIC_ENABLED,
        "available": available(),
        "chunks": len(_INDEX["chunks"]),
        "cached_vectors": len(_CACHE),
    }
