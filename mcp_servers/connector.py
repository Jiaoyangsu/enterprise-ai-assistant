"""Connector 生产化运行时：client_secret 鉴权 + 可选 TLS + 统一启动。

平台（WorkBuddy 连接器）硬性要求：
  - 传输：HTTPS + SSE / streamableHttp
  - 鉴权：client_secret（Bearer）
  - 单次工具调用 30 秒内响应

环境变量：
  CONNECTOR_CLIENT_SECRET   设置后所有请求必须携带 `Authorization: Bearer <secret>`。
                            未设置 = 本地开发模式（不鉴权，仅监听回环地址）。
  CONNECTOR_HOST            监听地址（默认：有 secret 时 0.0.0.0，否则 127.0.0.1）。
  CONNECTOR_TLS_CERT/KEY    证书与私钥路径；两者都设置则启用 HTTPS。
  CONNECTOR_MAX_CALL_SECONDS 单次调用建议上限（默认 30，仅用于启动自检提示）。

用法（各 server 的 __main__）：
    from connector import run
    run(mcp, port=8001)
"""
from __future__ import annotations

import hmac
import os

from fastmcp.server.auth import AccessToken, TokenVerifier


class ClientSecretVerifier(TokenVerifier):
    """静态 client_secret 校验（常量时间比较）。用于连接器 Bearer 鉴权。"""

    def __init__(self, secret: str, client_id: str = "workbuddy-connector"):
        super().__init__()
        self._secret = secret
        self._client_id = client_id

    async def verify_token(self, token: str) -> AccessToken | None:
        if token and hmac.compare_digest(str(token), self._secret):
            return AccessToken(token=token, client_id=self._client_id, scopes=["connector"])
        return None


def build_auth() -> TokenVerifier | None:
    """有 CONNECTOR_CLIENT_SECRET 才启用鉴权；否则返回 None（本地开发）。"""
    secret = (os.environ.get("CONNECTOR_CLIENT_SECRET") or "").strip()
    return ClientSecretVerifier(secret) if secret else None


def run(mcp, port: int, name: str | None = None) -> None:
    """按环境变量装配鉴权/TLS 并启动 streamable-http 服务。"""
    name = name or getattr(mcp, "name", "mcp")
    auth = build_auth()
    host = os.environ.get("CONNECTOR_HOST") or ("0.0.0.0" if auth else "127.0.0.1")

    if auth:
        mcp.auth = auth

    kwargs: dict = {"transport": "streamable-http", "host": host, "port": port}

    cert = os.environ.get("CONNECTOR_TLS_CERT")
    key = os.environ.get("CONNECTOR_TLS_KEY")
    scheme = "http"
    if cert and key:
        kwargs["uvicorn_config"] = {"ssl_certfile": cert, "ssl_keyfile": key}
        scheme = "https"

    max_call = os.environ.get("CONNECTOR_MAX_CALL_SECONDS", "30")
    print(f"[connector:{name}] {scheme}://{host}:{port} | "
          f"鉴权={'client_secret' if auth else 'off(dev)'} | 单次调用上限建议 {max_call}s")
    mcp.run(**kwargs)
