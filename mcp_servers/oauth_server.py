"""WorkBuddy 连接器 MCP 原生 OAuth 2.1（公共客户端 + PKCE）。

官方规范（https://open.workbuddy.cn/docs/connector）要点：
  - MCP 原生 OAuth 时 `connector-meta.json` 的 `auth_mode` **省略**；
  - 公共客户端 PKCE（S256），**不签发 client_secret**（`token_endpoint_auth_method=none`）；
  - 必需端点：`/.well-known/oauth-protected-resource`、`/.well-known/oauth-authorization-server`、
    `/oauth/register`（RFC 7591 动态注册）、`/oauth/authorize`、`/oauth/token`；
  - redirect_uri **精确匹配**，须支持 `workbuddy://workbuddy/mcp/connector%3A<source>/oauth/callback`
    与回退 `http://127.0.0.1:{动态端口}/oauth/callback`；
  - access_token ≈1h，refresh_token ≥30d，授权码一次性 ≈10min。

fastmcp 4.0.3 的 `InMemoryOAuthProvider` 已实现全部协议逻辑，但有三处与平台不吻合，
本模块在其上补齐：
  1. 默认端点是无 `/oauth` 前缀的 `/authorize`、`/token`…；这里在保留原路径的同时**增补
     `/oauth/*` 别名**，并按官方路径重写授权服务器元数据；
  2. 元数据 `token_endpoint_auth_methods_supported` 只列 `client_secret_*`，缺公共客户端
     必需的 `none`；
  3. `/.well-known/oauth-protected-resource`（无 `/mcp` 后缀）未注册；且 401 响应不带
     `WWW-Authenticate`（RFC 9728 发现入口）。
另外把 clients/tokens 落盘（0600，默认 `data/oauth_state.json`），进程重启不掉线。

环境变量：
  CONNECTOR_PUBLIC_URL        对外 HTTPS 根地址（OAuth 模式必填），如 https://kb.example.com
  CONNECTOR_OAUTH_SCOPES      逗号分隔授权项（默认见 DEFAULT_SCOPES）
  CONNECTOR_OAUTH_STATE       状态文件路径（默认 data/oauth_state.json）
  CONNECTOR_OAUTH_REDIRECT_URIS 额外允许的 redirect_uri（精确匹配，逗号分隔）
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    RefreshToken,
    RegistrationError,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull
from starlette.responses import JSONResponse
from starlette.routing import Route

DEFAULT_SCOPES = [
    "knowledge:read",
    "employee:read",
    "org:read",
    "budget:read",
    "contract:read",
    "pii:redact",
]

DEFAULT_STATE_FILE = "data/oauth_state.json"

_METADATA_PATH = "/.well-known/oauth-authorization-server"
_PRM_PATH = "/.well-known/oauth-protected-resource"
_ALIAS_PATHS = ("/authorize", "/token", "/register", "/revoke")


def _split_env(name: str) -> list[str]:
    raw = os.environ.get(name) or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _dump(models: dict[str, Any]) -> dict:
    out = {}
    for key, model in models.items():
        try:
            out[key] = model.model_dump(mode="json")
        except Exception:
            continue
    return out


class PersistentOAuthProvider(InMemoryOAuthProvider):
    """在 `InMemoryOAuthProvider` 上追加：持久化、`/oauth/*` 别名、公共客户端元数据。"""

    def __init__(
        self,
        *,
        base_url: str,
        scopes: list[str] | None = None,
        state_file: str | os.PathLike | None = None,
        extra_redirect_uris: list[str] | None = None,
    ):
        scopes = scopes or DEFAULT_SCOPES
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=scopes, default_scopes=scopes
            ),
            revocation_options=RevocationOptions(enabled=True),
        )
        self._state_path = Path(state_file or os.environ.get("CONNECTOR_OAUTH_STATE") or DEFAULT_STATE_FILE)
        self._extra_redirects = set(extra_redirect_uris or [])
        self._https_hosts = {urlparse(str(base_url)).hostname}
        self._load()

    # ---- 持久化 -------------------------------------------------------------
    def _load(self) -> None:
        raw = _load_state(self._state_path)
        for cid, data in (raw.get("clients") or {}).items():
            try:
                self.clients[cid] = OAuthClientInformationFull.model_validate(data)
            except Exception:
                continue
        for cid, data in (raw.get("auth_codes") or {}).items():
            try:
                self.auth_codes[cid] = AuthorizationCode.model_validate(data)
            except Exception:
                continue
        for tok, data in (raw.get("access_tokens") or {}).items():
            try:
                self.access_tokens[tok] = AccessToken.model_validate(data)
            except Exception:
                continue
        for tok, data in (raw.get("refresh_tokens") or {}).items():
            try:
                self.refresh_tokens[tok] = RefreshToken.model_validate(data)
            except Exception:
                continue

    def _save(self) -> None:
        payload = {
            "clients": _dump(self.clients),
            "auth_codes": _dump(self.auth_codes),
            "access_tokens": _dump(self.access_tokens),
            "refresh_tokens": _dump(self.refresh_tokens),
        }
        path = self._state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    # ---- redirect_uri 白名单 -------------------------------------------------
    def _redirect_allowed(self, uri: str) -> bool:
        uri = str(uri)
        if uri in self._extra_redirects:
            return True
        parsed = urlparse(uri)
        if parsed.scheme == "workbuddy" and parsed.netloc == "workbuddy":
            return True
        if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            return True
        if parsed.scheme == "https" and parsed.hostname in self._https_hosts:
            return True
        return False

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not self._redirect_allowed(uri):
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description=f"redirect_uri 不在允许范围：{uri}",
                )
        await super().register_client(client_info)
        self._save()

    async def authorize(self, client, params):  # noqa: ANN001
        result = await super().authorize(client, params)
        self._save()
        return result

    async def exchange_authorization_code(self, client, authorization_code):  # noqa: ANN001
        token = await super().exchange_authorization_code(client, authorization_code)
        self._save()
        return token

    async def exchange_refresh_token(self, client, refresh_token, scopes):  # noqa: ANN001
        token = await super().exchange_refresh_token(client, refresh_token, scopes)
        self._save()
        return token

    async def revoke_token(self, token) -> None:  # noqa: ANN001
        await super().revoke_token(token)
        self._save()

    # ---- 路由：/oauth/* 别名 + 元数据重写 ------------------------------------
    async def _metadata(self, request):  # noqa: ANN001
        root = str(self.base_url).rstrip("/")
        body = {
            "issuer": str(self.issuer_url).rstrip("/"),
            "authorization_endpoint": f"{root}/oauth/authorize",
            "token_endpoint": f"{root}/oauth/token",
            "registration_endpoint": f"{root}/oauth/register",
            "revocation_endpoint": f"{root}/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": [
                "none",
                "client_secret_post",
                "client_secret_basic",
            ],
            "scopes_supported": getattr(self, "scopes_supported", None) or DEFAULT_SCOPES,
            "resource": f"{root}/mcp",
        }
        return JSONResponse(body)

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes: list[Route] = []
        for route in super().get_routes(mcp_path):
            path = getattr(route, "path", "")
            if path == _METADATA_PATH:
                routes.append(
                    Route(path, endpoint=self._metadata, methods=["GET", "OPTIONS"], name="oauth-as-metadata")
                )
                continue
            routes.append(route)
            if path in _ALIAS_PATHS:
                routes.append(
                    Route(
                        "/oauth" + path,
                        endpoint=route.endpoint,
                        methods=list(route.methods or ["GET"]),
                        name="alias" + path.replace("/", "-"),
                    )
                )
            elif path == f"{_PRM_PATH}/mcp":
                routes.append(
                    Route(
                        _PRM_PATH,
                        endpoint=route.endpoint,
                        methods=list(route.methods or ["GET"]),
                        name="oauth-prm-bare",
                    )
                )
        return routes


class BearerChallengeMiddleware:
    """给 401 响应补 `WWW-Authenticate: Bearer resource_metadata=...`（RFC 9728 入口）。"""

    def __init__(self, app, resource_metadata_url: str):
        self.app = app
        self._url = resource_metadata_url

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):  # noqa: ANN001
            if message.get("type") == "http.response.start" and message.get("status") == 401:
                headers = list(message.get("headers") or [])
                if not any(k.lower() == b"www-authenticate" for k, _ in headers):
                    headers.append(
                        (b"www-authenticate", f'Bearer resource_metadata="{self._url}"'.encode())
                    )
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def public_url() -> str:
    return (os.environ.get("CONNECTOR_PUBLIC_URL") or "").strip().rstrip("/")


def build_oauth_provider() -> PersistentOAuthProvider | None:
    """CONNECTOR_PUBLIC_URL 设置时启用（MCP 原生 OAuth）；否则 None。"""
    base = public_url()
    if not base:
        return None
    return PersistentOAuthProvider(
        base_url=base,
        scopes=_split_env("CONNECTOR_OAUTH_SCOPES") or DEFAULT_SCOPES,
        extra_redirect_uris=_split_env("CONNECTOR_OAUTH_REDIRECT_URIS"),
    )
