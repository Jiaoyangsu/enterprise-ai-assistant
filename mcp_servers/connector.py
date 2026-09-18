"""Connector 生产化运行时：MCP 原生 OAuth（公共客户端 PKCE）+ 可选静态令牌 + TLS。

平台（WorkBuddy 连接器）硬性要求：
  - 传输：HTTPS + streamableHttp / SSE
  - 鉴权：MCP 原生 OAuth（公共客户端 + PKCE，`auth_mode` 省略），或静态 token（`auth_mode: token`）
  - 单次工具调用 30 秒内响应

两种鉴权模式：
  OAuth 模式（推荐，官方 MCP 原生 OAuth）
    设置 `CONNECTOR_PUBLIC_URL=https://<域名>` 即启用；端点与元数据见 `oauth_server.py`。
    兼容 `connector-meta.json` 省略 `auth_mode` 的要求。
  静态令牌模式（兼容旧包 / 内网联调）
    设置 `CONNECTOR_CLIENT_SECRET` 后，所有请求须带 `Authorization: Bearer <secret>`。

环境变量：
  CONNECTOR_PUBLIC_URL       对外 HTTPS 根地址 → 启用 OAuth（优先于静态令牌）
  CONNECTOR_CLIENT_SECRET    静态 Bearer 令牌（OAuth 未启用时生效）；都未设置 = 本地 dev
  CONNECTOR_HOST             监听地址（默认：有鉴权时 0.0.0.0，否则 127.0.0.1）
  CONNECTOR_TLS_CERT/KEY     证书与私钥路径；两者都设置则启用 HTTPS
  CONNECTOR_MAX_CALL_SECONDS 工具调用硬超时（默认 30；平台「单次 ≤30s」门槛）

用法（各 server 的 __main__）：
    from connector import run
    run(mcp, port=8001)          # 内部子服务：静态令牌/dev
    run(mcp, port=8000, oauth=True)  # 对外聚合端点：MCP 原生 OAuth
"""
from __future__ import annotations

import hmac
import os

import uvicorn
from fastmcp.server.auth import AccessToken, TokenVerifier

from oauth_server import BearerChallengeMiddleware, build_oauth_provider, public_url
from tool_timeout import ToolTimeoutMiddleware


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


def _max_call_seconds() -> float:
    try:
        return float(os.environ.get("CONNECTOR_MAX_CALL_SECONDS", "30"))
    except ValueError:
        return 30.0


def run(mcp, port: int, name: str | None = None, oauth: bool = False) -> None:
    """按环境变量装配鉴权/TLS/超时并启动 streamable-http 服务。"""
    name = name or getattr(mcp, "name", "mcp")
    seconds = _max_call_seconds()
    mcp.add_middleware(ToolTimeoutMiddleware(seconds))

    provider = build_oauth_provider() if oauth else None
    auth = provider or build_auth()
    host = os.environ.get("CONNECTOR_HOST") or ("0.0.0.0" if auth else "127.0.0.1")

    if oauth and provider is None:
        print("[connector] 提示：未设置 CONNECTOR_PUBLIC_URL，MCP 原生 OAuth 未启用；"
              "回退为静态令牌/dev 模式（本地联调可忽略，上架必须配 OAuth）")

    cert = os.environ.get("CONNECTOR_TLS_CERT")
    key = os.environ.get("CONNECTOR_TLS_KEY")
    scheme = "https" if (cert and key) else "http"
    if auth:
        mcp.auth = auth

    mode = "oauth(pkce)" if provider else ("client_secret" if auth else "off(dev)")
    print(f"[connector:{name}] {scheme}://{host}:{port} | "
          f"鉴权={mode} | 工具调用硬超时 {seconds:g}s")

    if provider is not None:
        base_app = mcp.http_app(transport="streamable-http")
        resource_url = f"{public_url()}/.well-known/oauth-protected-resource"
        app = BearerChallengeMiddleware(base_app, resource_url)
        uvicorn.run(app, host=host, port=port, ssl_certfile=cert, ssl_keyfile=key)
        return

    kwargs: dict = {"transport": "streamable-http", "host": host, "port": port}
    if cert and key:
        kwargs["uvicorn_config"] = {"ssl_certfile": cert, "ssl_keyfile": key}
    mcp.run(**kwargs)
