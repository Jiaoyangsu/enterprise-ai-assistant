"""对外公开的非 MCP 端点：`/healthz`（可用性监控）与 `/privacy`（隐私政策页面）。

连接器只需一个 HTTPS 源，因此把监控与合规页面挂在同一 ASGI 应用上，避免再维护
一个静态站点：

  GET /healthz   → 200 JSON（服务名/版本/鉴权模式/时间戳/进程启动秒数），供探针轮询
  GET /privacy   → 200 HTML 隐私政策页；可用 `CONNECTOR_PRIVACY_FILE` 指向自有
                   HTML 覆盖（法务终稿），默认模板含 `${OPERATOR}`/`${CONTACT}` 占位

中间件位于最外层，先于鉴权拦截，因此探针无需令牌；页面不含任何凭证。
环境变量：
  CONNECTOR_PRIVACY_FILE   自有隐私政策 HTML 路径（覆盖内置模板）
  CONNECTOR_OPERATOR       运营主体名称（默认「企业知识库助手运营方」）
  CONNECTOR_PRIVACY_CONTACT 隐私事务联系邮箱（默认 support@example.com，上线须替换）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_START = time.time()

_DEFAULT_PRIVACY_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>隐私政策 · ${SERVICE}</title>
<style>body{max-width:820px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui,-apple-system,"PingFang SC",sans-serif;color:#1f2328}h1{font-size:24px}h2{font-size:18px;margin-top:1.6em}code{background:#f2f3f5;padding:2px 5px;border-radius:4px}</style>
</head>
<body>
<h1>${SERVICE} 隐私政策</h1>
<p>运营主体：${OPERATOR}　联系方式：<a href="mailto:${CONTACT}">${CONTACT}</a></p>
<p>本页为连接器对外提供的隐私政策入口。正式对外发布前，须由法务以终稿替换本模板（见
<code>CONNECTOR_PRIVACY_FILE</code>）。</p>
<h2>一、我们处理哪些数据</h2>
<p>连接器仅在企业内网处理被授权访问的文档、员工/部门/预算/合同等业务记录，以及在
企业服务器本地生成并存储的向量索引。所有模型推理与数据检索均在本地完成，不向第三方
传输你的业务数据。</p>
<h2>二、鉴权与授权</h2>
<p>连接器使用 MCP 原生 OAuth 2.1（公共客户端 + PKCE）。我们不收集、不存储你的第三方账号
口令；访问令牌由平台签发并可在授权过期后自动续期或撤销。</p>
<h2>三、日志与审计</h2>
<p>为满足安全合规，我们会记录工具调用审计（调用者、时间、工具名、参数与结果的脱敏摘要
及密级标记）。审计日志写入受管目录、仅服务账号可读写，并按容量轮转、限期保留；其中的
个人敏感信息（手机号、身份证、邮箱、银行卡、地址、内部人名）在落盘前即被脱敏。</p>
<h2>四、数据保留与删除</h2>
<p>审计日志按运营策略限期保留，到期自动滚动删除。如需查询、更正或删除你的数据，请通过
上方联系方式提出。</p>
<h2>五、数据驻留</h2>
<p>数据存储与处理均在企业自有服务器完成；默认不启用任何外部网络回退。</p>
<p style="margin-top:2.5em;color:#6b7280;font-size:14px">更新日期：见本页部署时间。本页仅为信息展示，不构成法律意见。</p>
</body>
</html>
"""


def _privacy_html() -> bytes:
    custom = (os.environ.get("CONNECTOR_PRIVACY_FILE") or "").strip()
    if custom:
        try:
            return Path(custom).read_bytes()
        except OSError:
            pass
    html = (_DEFAULT_PRIVACY_HTML
            .replace("${SERVICE}", "企业知识库助手")
            .replace("${OPERATOR}", os.environ.get("CONNECTOR_OPERATOR") or "企业知识库助手运营方")
            .replace("${CONTACT}", os.environ.get("CONNECTOR_PRIVACY_CONTACT") or "support@example.com"))
    return html.encode("utf-8")


def _health() -> bytes:
    body = {
        "status": "ok",
        "service": os.environ.get("CONNECTOR_SERVICE") or "enterprise-knowledge-assistant",
        "auth": os.environ.get("CONNECTOR_AUTH_MODE") or "unknown",
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "uptime_s": round(time.time() - _START, 3),
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


async def _respond(send, status: int, content_type: str, body: bytes, *, head: bool) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", content_type.encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": b"" if head else body})


class PublicInfoMiddleware:
    """在最外层拦截 `/healthz`(`/health`) 与 `/privacy`(`/privacy-policy`)，其余透传。"""

    def __init__(self, app, service: str | None = None):
        self.app = app
        if service:
            os.environ.setdefault("CONNECTOR_SERVICE", service)

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and scope.get("method") in ("GET", "HEAD"):
            path = (scope.get("path") or "").rstrip("/") or "/"
            head = scope["method"] == "HEAD"
            if path in ("/healthz", "/health"):
                return await _respond(send, 200, "application/json; charset=utf-8", _health(), head=head)
            if path in ("/privacy", "/privacy-policy"):
                return await _respond(send, 200, "text/html; charset=utf-8", _privacy_html(), head=head)
        await self.app(scope, receive, send)
