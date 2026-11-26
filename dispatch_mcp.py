"""在多模型对话中按本轮选择使用已安装的 MCP。"""

import asyncio
from contextlib import AsyncExitStack


async def installed_plugins(storage):
    if storage is None:
        return []
    return [record for record in await storage.list_mcps("local")
            if record.enabled and record.client.trust_annotations and record.hub_id != "aoi-local"]


class DispatchMCP:
    def __init__(self, records):
        self.records = {record.id: record for record in records}
        self.connections = {}
        self.stack = AsyncExitStack()
        self.secrets = set()
        for record in records:
            config = record.client.mcp_config
            for values in (record.values, getattr(config, "headers", {}), getattr(config, "env", {})):
                for key, value in (values or {}).items():
                    if isinstance(value, str) and value and any(word in key.lower() for word in (
                        "key", "token", "auth", "secret", "password",
                    )):
                        self.secrets.add(value)
                        if value.lower().startswith("bearer "):
                            self.secrets.add(value[7:].strip())

    def catalog(self):
        return [{"id": record.id, "name": record.display_name or record.name} for record in self.records.values()]

    async def execute(self, request):
        if not request.plugin:
            return {"ok": True, "plugins": self.catalog()}
        record = self.records.get(request.plugin)
        if record is None:
            raise ValueError("此 MCP 未在本轮启用。")
        async with asyncio.timeout(45):
            try:
                if record.id not in self.connections:
                    client = record.client.model_copy(update={"stream_progress": False})
                    await client.connect()
                    self.stack.push_async_callback(client.close)
                    self.connections[record.id] = client
                client = self.connections[record.id]
                tools = {tool.name: tool for tool in await client.list_raw_tools()
                         if tool.annotations and tool.annotations.read_only_hint and not tool.annotations.destructive_hint}
                if not request.tool:
                    return {"ok": True, "tools": [
                        {"name": tool.name, "description": (tool.description or "")[:1000],
                         "parameters": tool.input_schema}
                        for tool in list(tools.values())[:25]
                    ]}
                if request.tool not in tools:
                    raise ValueError("此工具未启用或未标为只读。")
                tool = await client.get_tool(request.tool)
                result = await tool(**request.arguments)
                output = "\n".join(block.text for block in result.content if block.type == "text")[:24000]
                return {"ok": result.is_last and result.state in {"running", "success"},
                        "output": output or "工具未返回文本。"}
            except (ValueError, TimeoutError):
                raise
            except Exception:
                raise ValueError("MCP 调用失败，请在 MCP 中心检查连接和配置。") from None

    async def close(self):
        await self.stack.aclose()
