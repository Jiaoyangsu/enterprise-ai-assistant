"""MCP 工具调用硬超时中间件（连接器「单次 ≤30s」硬门槛）。

平台要求单次工具调用 30 秒内响应（见 `docs/上架类目与资质.md`）。这里在 MCP
协议层拦截每一次 `tools/call`，用 `asyncio.wait_for` 兜底：超时即以可读错误返回，
而不是让请求一直挂着。

用法（在 server 启动时装配）：

    from tool_timeout import ToolTimeoutMiddleware
    mcp.add_middleware(ToolTimeoutMiddleware(30))
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
import mcp.types as mt


class ToolTimeoutMiddleware(Middleware):
    """给每次工具调用套一个墙钟超时；超时抛 `ToolError`（对模型可读）。"""

    def __init__(self, seconds: float = 30.0):
        if seconds <= 0:
            raise ValueError("seconds 必须大于 0")
        self.seconds = float(seconds)

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, Any],
    ) -> Any:
        name = getattr(context.message, "name", None) or "tool"
        try:
            return await asyncio.wait_for(call_next(context), timeout=self.seconds)
        except asyncio.TimeoutError:
            raise ToolError(
                f"工具 {name} 超过 {self.seconds:g}s 未返回，已中止（平台单次调用上限 30s）。"
                "请缩小查询范围或稍后重试。"
            ) from None
