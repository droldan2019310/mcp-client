# mcp_client.py
import os, httpx, asyncio

class MCPClientHTTP:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.tools = []

    async def initialize(self):
        payload = {"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base_url}/mcp", json=payload)
            r.raise_for_status()
            return r.json()

    async def list_tools(self):
        payload = {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base_url}/mcp", json=payload)
            r.raise_for_status()
            data = r.json()
        self.tools = data.get("result", {}).get("tools", [])
        return self.tools

    async def call_tool(self, name: str, arguments: dict):
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments}
        }
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.base_url}/mcp", json=payload)
            r.raise_for_status()
            return r.json()

async def bootstrap_clients():
    local_url = os.getenv("MCP_LOCAL_URL", "http://localhost:8080/mcp")
    remote_url = os.getenv("MCP_REMOTE_URL", "")
    clients = {}

    local = MCPClientHTTP(local_url)
    await local.initialize()
    await local.list_tools()
    clients["local"] = local

    if remote_url:
        remote = MCPClientHTTP(remote_url)
        await remote.initialize()
        await remote.list_tools()
        clients["remote"] = remote

    return clients
