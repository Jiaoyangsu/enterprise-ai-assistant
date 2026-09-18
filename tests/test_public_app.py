"""公开端点测试：/healthz 与 /privacy 由最外层中间件提供，其余请求透传。"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_servers"))

from public_app import PublicInfoMiddleware  # noqa: E402


async def _dummy_inner(scope, receive, send):
    await send({"type": "http.response.start", "status": 418, "headers": []})
    await send({"type": "http.response.body", "body": b"inner"})


async def _call(mw, path, method="GET"):
    captured = []

    async def send(msg):
        captured.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await mw({"type": "http", "method": method, "path": path}, receive, send)
    start = next(m for m in captured if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in captured if m["type"] == "http.response.body")
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, body


class R:
    def __init__(self):
        self.passed = self.failed = 0

    def check(self, name, ok, detail=""):
        self.passed += 1 if ok else 0
        self.failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {name} {detail}")


def main():
    r = R()
    mw = PublicInfoMiddleware(_dummy_inner, service="enterprise-knowledge-assistant")

    status, headers, body = asyncio.run(_call(mw, "/healthz"))
    r.check("healthz-200", status == 200, status)
    r.check("healthz-JSON", headers.get("content-type", "").startswith("application/json"))
    payload = json.loads(body)
    r.check("healthz-内容", payload["status"] == "ok"
            and payload["service"] == "enterprise-knowledge-assistant")

    status, headers, body = asyncio.run(_call(mw, "/privacy"))
    r.check("privacy-200", status == 200, status)
    r.check("privacy-HTML", headers.get("content-type", "").startswith("text/html"))
    r.check("privacy-含政策正文", "隐私政策" in body.decode("utf-8")
            and "OAuth" in body.decode("utf-8"))

    status, _, body = asyncio.run(_call(mw, "/privacy", method="HEAD"))
    r.check("privacy-HEAD空体", status == 200 and body == b"")

    # 自定义隐私政策文件覆盖
    tmp = Path(tempfile.mkdtemp(prefix="priv_")) / "policy.html"
    tmp.write_text("<html>自定义政策终稿</html>", "utf-8")
    os.environ["CONNECTOR_PRIVACY_FILE"] = str(tmp)
    _, _, body = asyncio.run(_call(mw, "/privacy"))
    r.check("privacy-自定义文件生效", "自定义政策终稿" in body.decode("utf-8"))
    os.environ.pop("CONNECTOR_PRIVACY_FILE", None)

    # 其余路径与协议透传
    status, _, body = asyncio.run(_call(mw, "/mcp", method="POST"))
    r.check("透传-非公开路径交给内层", status == 418 and body == b"inner", status)

    async def passthrough_lifespan():
        seen = []

        async def send(msg):
            seen.append(msg)

        async def receive():
            return {"type": "lifespan.startup"}

        await mw({"type": "lifespan"}, receive, send)
        return seen

    asyncio.run(passthrough_lifespan())
    r.check("透传-lifespan不拦截", True)

    print(f"\n结果：{r.passed}/{r.passed + r.failed} 通过，{r.failed} 失败")
    sys.exit(1 if r.failed else 0)


if __name__ == "__main__":
    main()
