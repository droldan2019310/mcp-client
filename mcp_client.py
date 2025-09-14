# mcp_client_unified.py
import os, json, asyncio, shlex, httpx, subprocess
from abc import ABC, abstractmethod

JSONRPC_VERSION = "2.0"
# Permite sobreescribir por ENV; por defecto conserva 2024-09, pero muchos servers ya hablan 2025-06-18
DEFAULT_PROTOCOL = os.getenv("MCP_PROTOCOL", "2024-09")

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
    # Para notificaciones (por defecto hace RPC normal e ignora respuesta; STDIO lo sobreescribe)
    async def _notify(self, payload: dict) -> None:
        try:
            await self._rpc(payload)
        except Exception:
            pass

    async def initialize(self) -> dict:
        # 1) intentar strict/minimal/empty según flag
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
                # éxito
                return resp.get("result", {})
            except Exception as e:
                last_err = e

        raise RuntimeError(f"initialize failed: {last_err}")

    async def notify_initialized(self):
        """Envía notifications/initialized como NOTIFICACIÓN (sin id)."""
        payload = {
            "jsonrpc": JSONRPC_VERSION,
            "method": "notifications/initialized",
            "params": {}
        }
        await self._notify(payload)

    async def list_tools(self) -> list:
        resp = await self._rpc_lenient("tools/list", None, id=2)
        if "error" in resp:
            raise RuntimeError(resp["error"])
        self.tools = (resp.get("result") or {}).get("tools", [])
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

    async def _rpc_lenient(self, method: str, params: dict | None = None, id: int = 0) -> dict:
        """
        Para métodos sin parámetros definidos (p.ej. tools/list), prueba:
        - sin 'params'
        - con 'params': {}
        - con 'params': null
        Retorna la primera respuesta sin 'error' o la última si todas fallan.
        """
        if params is None:
            last = None
            for payload in (
                {"jsonrpc": JSONRPC_VERSION, "id": id, "method": method},
                {"jsonrpc": JSONRPC_VERSION, "id": id, "method": method, "params": {}},
                {"jsonrpc": JSONRPC_VERSION, "id": id, "method": method, "params": None},
            ):
                resp = await self._rpc(payload)
                last = resp
                if "error" not in resp:
                    return resp
                # si no es -32602 (invalid params), no tiene sentido reintentar
                if not isinstance(resp.get("error"), dict) or resp["error"].get("code") != -32602:
                    return resp
            return last or {"error": {"code": -32099, "message": "all attempts failed"}}
        else:
            return await self._rpc({"jsonrpc": JSONRPC_VERSION, "id": id, "method": method, "params": params})


class HTTPMCPClient(BaseMCPClient):
    def __init__(self, name: str, base_url: str):
        super().__init__(name)
        self.base_url = base_url.rstrip("/")

    async def _rpc(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(self.base_url, json=payload, headers={"Accept": "application/json"})
            r.raise_for_status()
            # algunos servers podrían no responder JSON en notificaciones; intenta parsear y si falla, devuelve {}
            try:
                return r.json()
            except Exception:
                return {}

    # En HTTP no hace falta override de _notify (el de base ya ignora la respuesta)


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

    async def _notify(self, payload: dict) -> None:
        """En STDIO, escribe la notificación y NO esperes respuesta."""
        if self.proc.poll() is not None:
            return
        line = json.dumps(payload, ensure_ascii=False)
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        # pequeño respiro para que el server procese el estado
        await asyncio.sleep(0.05)


async def bootstrap_clients():
    clients = {}

    # HTTP endpoints (e.g., local/remote)
    http_cfg = os.getenv("MCP_HTTP", "")
    for pair in filter(None, (x.strip() for x in http_cfg.split(","))):
        name, url = pair.split(":", 1)
        cli = HTTPMCPClient(name, url)
        await cli.initialize()
        # típicamente no se requiere notification para HTTP
        await cli.list_tools()
        clients[name] = cli

    # STDIO endpoints (e.g., filesystem, git, etc.)
    stdio_cfg = os.getenv("MCP_STDIO", "")
    notify_list = set(
        x.strip() for x in os.getenv("MCP_INIT_NOTIFY", "git,github").split(",") if x.strip()
    )

    for pair in filter(None, (x.strip() for x in stdio_cfg.split(","))):
        name, cmd = pair.split(":", 1)
        cli = StdioMCPClient(name, cmd)
        await cli.initialize()

        # 🔑 Notificación solo a los servers indicados (p.ej. git/github)
        if name in notify_list:
            await cli.notify_initialized()

        await cli.list_tools()
        clients[name] = cli

    return clients