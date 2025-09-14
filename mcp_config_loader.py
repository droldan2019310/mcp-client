# mcp_config_loader.py
import json, os, shlex
from typing import Dict, Any
from mcp_client import HTTPMCPClient, StdioMCPClient

def _expand_env(s: str) -> str:
    return os.path.expandvars(os.path.expanduser(s))

def load_mcp_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    cfg = json.loads(raw)
    return cfg

async def build_clients_from_config(cfg: Dict[str, Any]):
    mcp_servers = cfg.get("mcpServers", {})
    clients = {}
    for name, spec in mcp_servers.items():
        t = spec.get("transport", "stdio").lower()
        if t == "http":
            base = spec["url"].rstrip("/")
            cli = HTTPMCPClient(name, base)
        else:
            cmd = spec["command"]
            args = spec.get("args", [])
            # Expande variables/paths
            cmd = _expand_env(cmd)
            args = [_expand_env(a) for a in args]
            full_cmd = " ".join([shlex.quote(cmd), *map(shlex.quote, args)])
            cli = StdioMCPClient(name, full_cmd)

        await cli.initialize()
        await cli.list_tools()
        clients[name] = cli
    return clients
