# mcp_client_unified.py
import os, json, asyncio, shlex, httpx, subprocess
from abc import ABC, abstractmethod

JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL = "2024-09"

INIT_STRICT = {
    "jsonrpc": JSONRPC_VERSION,
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": DEFAULT_PROTOCOL,
        "capabilities": { "tools": {}, "resources": {}, "prompts": {} },
        "clientInfo": { "name": "mcp-chatbot", "version": "0.1.0" }
    }
}

INIT_MINIMAL = {
    "jsonrpc": JSONRPC_VERSION,
    "id": 1,
    "method": "initialize",
    "params": {}
}

INIT_EMPTY = {
    "jsonrpc": JSONRPC_VERSION,
    "id": 1,
    "method": "initialize"
}

class BaseMCPClient(ABC):
    def __init__(self, name: str):
        self.name = name
        self.tools = []
        # puedes forzar modo por env: MCP_INIT_STRICT=0/1
        self.strict_init = os.getenv("MCP_INIT_STRICT", "1") == "1"

    @abstractmethod
    async def _rpc(self, payload: dict) -> dict: ...

    async def initialize(self) -> dict:
        # 1) intentar strict
        try_orders = []
        if self.strict_init:
            try_orders = [INIT_STRICT, INIT_MINIMAL, INIT_EMPTY]
        else:
            try_orders = [INIT_MINIMAL, INIT_EMPTY, INIT_STRICT]

        last_err = None
        for init_payload in try_orders:
            try:
                resp = await self._rpc(init_payload)
                if "error" in resp:
                    last_err = resp["error"]
                    continue
                return resp["result"]
            except Exception as e:
                last_err = e

        raise RuntimeError(f"initialize failed: {last_err}")

    async def list_tools(self) -> list:
        resp = await self._rpc({
            "jsonrpc": JSONRPC_VERSION, "id": 2,
            "method": "tools/list", "params": {}
        })
        if "error" in resp:
            raise RuntimeError(resp["error"])
        self.tools = resp["result"]["tools"]
        return self.tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        payload = {
            "jsonrpc": JSONRPC_VERSION, "id": 3, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}
        }
        resp = await self._rpc(payload)
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp

class HTTPMCPClient(BaseMCPClient):
    def __init__(self, name: str, base_url: str):
        super().__init__(name)
        self.base_url = base_url.rstrip("/")

    async def _rpc(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.base_url}", json=payload)
            r.raise_for_status()
            return r.json()

class StdioMCPClient(BaseMCPClient):
    def __init__(self, name: str, cmd: str):
        super().__init__(name)
        self.proc = subprocess.Popen(
            shlex.split(cmd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # si quieres ver logs, usa subprocess.STDOUT
            text=True,
            bufsize=1
        )

    async def _read_json_line(self) -> dict:
        # Ignora banners/líneas no-JSON
        loop = asyncio.get_event_loop()
        for _ in range(500):
            raw = await loop.run_in_executor(None, self.proc.stdout.readline)
            if not raw:
                raise RuntimeError("no stdout from stdio server")
            s = raw.strip()
            if not s:
                continue
            if s[0] in "{[":
                try:
                    return json.loads(s)
                except json.JSONDecodeError:
                    continue
            # línea no JSON; sigue leyendo
        raise RuntimeError("gave up reading JSON from stdio server")

    async def _rpc(self, payload: dict) -> dict:
        if self.proc.poll() is not None:
            raise RuntimeError(f"{self.name} process exited")
        line = json.dumps(payload, ensure_ascii=False)
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return await self._read_json_line()

async def bootstrap_clients():
    clients = {}

    # HTTP endpoints (e.g., local/remote)
    http_cfg = os.getenv("MCP_HTTP", "")
    for pair in filter(None, (x.strip() for x in http_cfg.split(","))):
        name, url = pair.split(":", 1)
        cli = HTTPMCPClient(name, url)
        await cli.initialize()
        await cli.list_tools()
        clients[name] = cli

    # STDIO endpoints (e.g., filesystem, git, etc.)
    stdio_cfg = os.getenv("MCP_STDIO", "")
    for pair in filter(None, (x.strip() for x in stdio_cfg.split(","))):
        name, cmd = pair.split(":", 1)
        cli = StdioMCPClient(name, cmd)
        await cli.initialize()
        await cli.list_tools()
        clients[name] = cli

    return clients
