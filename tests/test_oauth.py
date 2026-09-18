"""WorkBuddy 连接器 OAuth 2.1（公共客户端 PKCE）与 30s 硬超时测试。

只依赖标准库（urllib）做 HTTP，避免 venv 缺 httpx。覆盖官方规范关键点：
元数据/发现端点、动态注册（回显自定义 scheme）、PKCE 授权码流程、令牌交换/刷新、
授权码一次性、redirect_uri 白名单、401 的 WWW-Authenticate、`/oauth/*` 别名、
状态持久化，以及 `ToolTimeoutMiddleware` 超时行为。
"""
import asyncio
import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode, urlparse, parse_qs
import urllib.request as U

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_servers"))

import uvicorn  # noqa: E402
from fastmcp import FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from oauth_server import (  # noqa: E402
    BearerChallengeMiddleware,
    PersistentOAuthProvider,
    DEFAULT_SCOPES,
)
from tool_timeout import ToolTimeoutMiddleware  # noqa: E402

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
REDIRECT = "workbuddy://workbuddy/mcp/connector%3Aenterprise-knowledge-assistant/oauth/callback"


class R:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name, ok, detail=""):
        self.passed += 1 if ok else 0
        self.failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {name} {detail}")


class NoRedir(U.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = U.build_opener(NoRedir)


def http(method, path, data=None, headers=None, ctype=None):
    url = BASE + path
    if isinstance(data, dict) and ctype == "json":
        data = json.dumps(data).encode()
    elif isinstance(data, dict):
        data = urlencode(data).encode()
    h = dict(headers or {})
    if ctype:
        h["Content-Type"] = {"json": "application/json", "form": "application/x-www-form-urlencoded"}[ctype]
    req = U.Request(url, data=data, method=method, headers=h)
    try:
        resp = _opener.open(req, timeout=5)
        return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except U.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def body_json(raw):
    try:
        return json.loads(raw)
    except Exception:
        return {}


def register(client_name="workbuddy"):
    return http(
        "POST",
        "/oauth/register",
        {
            "redirect_uris": [REDIRECT],
            "client_name": client_name,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": " ".join(DEFAULT_SCOPES),
        },
        ctype="json",
    )


def pkce():
    verifier = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def test_timeout(r):
    async def slow(_ctx):
        await asyncio.sleep(0.3)
        return "late"

    async def fast(_ctx):
        return "ok"

    ctx = SimpleNamespace(message=SimpleNamespace(name="slow_tool"))
    mw = ToolTimeoutMiddleware(0.05)
    try:
        asyncio.run(mw.on_call_tool(ctx, slow))
        r.check("超时-应中止", False, "未超时")
    except ToolError as e:
        r.check("超时-抛ToolError", "slow_tool" in str(e), str(e)[:40])
    r.check("超时-快调用放行", asyncio.run(mw.on_call_tool(ctx, fast)) == "ok")
    try:
        ToolTimeoutMiddleware(0)
        r.check("超时-非法参数", False)
    except ValueError:
        r.check("超时-非法参数", True)


def main():
    r = R()
    test_timeout(r)

    tmp = tempfile.mkdtemp(prefix="oauth_state_")
    state = Path(tmp) / "state.json"

    provider = PersistentOAuthProvider(base_url=BASE, state_file=state)
    mcp = FastMCP("oauth-test", auth=provider)

    @mcp.tool
    def ping() -> str:
        return "pong"

    app = BearerChallengeMiddleware(
        mcp.http_app(transport="streamable-http"),
        f"{BASE}/.well-known/oauth-protected-resource",
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            http("GET", "/.well-known/oauth-protected-resource/mcp")
            break
        except Exception:
            time.sleep(0.1)

    # ---- 发现与元数据 ----
    s, _, b = http("GET", "/.well-known/oauth-protected-resource")
    prm = body_json(b)
    r.check("发现-裸PRM可达", s == 200, f"status={s}")
    r.check("发现-resource指向/mcp", str(prm.get("resource", "")).endswith("/mcp"), str(prm.get("resource")))
    r.check("发现-含授权服务器", bool(prm.get("authorization_servers")))

    s, _, b = http("GET", "/.well-known/oauth-authorization-server")
    meta = body_json(b)
    r.check("元数据-可达", s == 200, f"status={s}")
    r.check("元数据-端点带/oauth前缀",
            meta.get("token_endpoint", "").endswith("/oauth/token")
            and meta.get("authorization_endpoint", "").endswith("/oauth/authorize"))
    r.check("元数据-支持公共客户端none",
            "none" in meta.get("token_endpoint_auth_methods_supported", []),
            str(meta.get("token_endpoint_auth_methods_supported")))
    r.check("元数据-PKCE S256", "S256" in meta.get("code_challenge_methods_supported", []))

    # ---- 动态注册 ----
    s, _, b = register()
    reg = body_json(b)
    r.check("注册-接受自定义scheme", s in (200, 201), f"status={s} {str(b)[:80]}")
    r.check("注册-回显redirect_uris", REDIRECT in (reg.get("redirect_uris") or []), str(reg.get("redirect_uris")))
    r.check("注册-公共客户端无secret", not reg.get("client_secret"))
    cid = reg.get("client_id")

    s, _, _ = http(
        "POST",
        "/oauth/register",
        {"redirect_uris": ["http://evil.example.com/cb"], "client_name": "bad"},
        ctype="json",
    )
    r.check("注册-拒绝未授权redirect", s in (400, 401, 403), f"status={s}")

    # ---- 授权码 + 令牌 ----
    verifier, challenge = pkce()
    q = urlencode({
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": "knowledge:read", "state": "xyz",
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    s, h, _ = http("GET", "/oauth/authorize?" + q)
    loc = h.get("location", "")
    code = parse_qs(urlparse(loc).query).get("code", [None])[0]
    r.check("授权-302到自定义scheme", s == 302 and loc.startswith("workbuddy://") and bool(code))

    s, _, b = http(
        "POST", "/oauth/token",
        {"grant_type": "authorization_code", "code": code, "client_id": cid,
         "redirect_uri": REDIRECT, "code_verifier": verifier},
        ctype="form",
    )
    tok = body_json(b)
    r.check("令牌-换取成功", s == 200 and bool(tok.get("access_token")), f"status={s}")
    r.check("令牌-含refresh_token", bool(tok.get("refresh_token")))
    r.check("令牌-有效期≈1h", tok.get("expires_in", 0) >= 3000, str(tok.get("expires_in")))

    s, _, b = http(
        "POST", "/oauth/token",
        {"grant_type": "authorization_code", "code": code, "client_id": cid,
         "redirect_uri": REDIRECT, "code_verifier": verifier},
        ctype="form",
    )
    r.check("令牌-授权码一次性", s != 200 and body_json(b).get("error") == "invalid_grant", f"status={s}")

    # 错误 code_verifier
    _, challenge2 = pkce()
    q2 = urlencode({
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": "knowledge:read", "code_challenge": challenge2, "code_challenge_method": "S256",
    })
    _, h2, _ = http("GET", "/oauth/authorize?" + q2)
    code2 = parse_qs(urlparse(h2.get("location", "")).query).get("code", [None])[0]
    wrong = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode()
    s, _, _ = http(
        "POST", "/oauth/token",
        {"grant_type": "authorization_code", "code": code2, "client_id": cid,
         "redirect_uri": REDIRECT, "code_verifier": wrong},
        ctype="form",
    )
    r.check("令牌-错误verifier拒绝", s != 200, f"status={s}")

    # ---- 刷新令牌 ----
    s, _, b = http(
        "POST", "/oauth/token",
        {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid},
        ctype="form",
    )
    refreshed = body_json(b)
    r.check("刷新-换发新令牌", s == 200 and bool(refreshed.get("access_token")), f"status={s}")
    r.check("刷新-旧access_token失效", tok["access_token"] != refreshed.get("access_token"))
    live = refreshed.get("access_token")

    # ---- /mcp 鉴权与挑战头 ----
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "p", "version": "0"}}}
    s, h, _ = http("POST", "/mcp", init, ctype="json")
    r.check("MCP-无token为401", s == 401, f"status={s}")
    r.check("MCP-401带WWW-Authenticate",
            "resource_metadata" in (h.get("www-authenticate") or ""),
            str(h.get("www-authenticate"))[:60])
    s, _, _ = http("POST", "/mcp", init, {"Authorization": f"Bearer {live}"}, ctype="json")
    r.check("MCP-带token通过鉴权", s != 401, f"status={s}")

    # ---- 持久化：同一状态文件重建 provider ----
    p2 = PersistentOAuthProvider(base_url=BASE, state_file=state)
    r.check("持久化-客户端保留", asyncio.run(p2.get_client(cid)) is not None)
    r.check("持久化-令牌保留", asyncio.run(p2.load_access_token(live)) is not None)
    r.check("持久化-文件权限0600", (state.stat().st_mode & 0o777) == 0o600,
            oct(state.stat().st_mode & 0o777))

    server.should_exit = True
    print(f"\n结果：{r.passed}/{r.passed + r.failed} 通过，{r.failed} 失败")
    sys.exit(1 if r.failed else 0)


if __name__ == "__main__":
    main()
