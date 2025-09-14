#!/usr/bin/env python3
# git_mcp_probe_min.py
# initialize -> (notification) notifications/initialized -> tools/list -> (opcional) tools/call

import argparse, json, shlex, subprocess, sys, time

JSONRPC = "2.0"
PROTOCOLS = ["2025-06-18", "2024-09"]

def send_line_and_wait(p, payload, timeout=30):
    """Envía un request (con id) y espera UNA línea JSON de respuesta."""
    p.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    p.stdin.flush()
    t0 = time.time()
    while True:
        if time.time() - t0 > timeout:
            return {"error": {"code": -32091, "message": "timeout"}}
        raw = p.stdout.readline()
        if not raw:
            time.sleep(0.01); continue
        s = raw.strip()
        if not s:
            continue
        if s[:1] in "{[":
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                continue
        print("[git STDIO]", s)  # logs no-JSON

def send_notify_initialized(p):
    """Envía NOTIFICACIÓN (sin id) y NO espera respuesta."""
    payload = {"jsonrpc": JSONRPC, "method": "notifications/initialized", "params": {}}
    p.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    p.stdin.flush()

def initialize(p):
    last = None
    for proto in PROTOCOLS:
        req = {
            "jsonrpc": JSONRPC, "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": proto,
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "clientInfo": {"name": "git-mcp-probe", "version": "0.0.1"},
            }
        }
        resp = send_line_and_wait(p, req)
        if "error" not in resp:
            return resp
        last = resp
        print("initialize FAILED with", proto, "→", resp.get("error"))
    return last or {"error": {"code": -32099, "message": "init failed"}}

def tools_list_lenient(p):
    # intenta sin params, luego {} y null, con pequeño backoff por si el server
    # necesita un micro-tick tras la notificación
    attempts = (
        {"jsonrpc": JSONRPC, "id": 2, "method": "tools/list"},
        {"jsonrpc": JSONRPC, "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": JSONRPC, "id": 2, "method": "tools/list", "params": None},
    )
    last = None
    for i, req in enumerate(attempts, 1):
        if i > 1:
            time.sleep(0.05)
        resp = send_line_and_wait(p, req)
        last = resp
        if "error" not in resp:
            return resp
        err = resp.get("error") or {}
        if err.get("code") != -32602:
            return resp
    return last

def tools_call(p, name, args):
    req = {"jsonrpc": JSONRPC, "id": 3, "method": "tools/call",
           "params": {"name": name, "arguments": args}}
    return send_line_and_wait(p, req)

def main():
    ap = argparse.ArgumentParser(description="Probe STDIO para mcp-server-git.")
    ap.add_argument("--repo", required=True, help="Ruta ABSOLUTA al repo git")
    ap.add_argument("--py", required=True, help="Ruta ABSOLUTA al python (venv/bin/python)")
    ap.add_argument("--tool", help="(opcional) tool a invocar")
    ap.add_argument("--args", default="{}", help="JSON para --tool")
    a = ap.parse_args()

    cmd = f'{a.py} -m mcp_server_git --repository "{a.repo}"'
    print("→ STDIO cmd:", cmd)
    p = subprocess.Popen(
        shlex.split(cmd),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1
    )

    try:
        # 1) initialize (request con id, esperamos respuesta)
        init = initialize(p)
        print("\n=== initialize ===")
        print(json.dumps(init, indent=2, ensure_ascii=False))
        if "error" in init: sys.exit(2)

        # 2) notifications/initialized (NOTIFICACIÓN sin id, no esperamos respuesta)
        send_notify_initialized(p)
        # pequeño respiro
        time.sleep(0.05)

        # 3) tools/list (tolerante)
        tl = tools_list_lenient(p)
        print("\n=== tools/list ===")
        print(json.dumps(tl, indent=2, ensure_ascii=False))
        if "error" in tl: sys.exit(3)

        # 4) opcional: tools/call
        if a.tool:
            try:
                call_args = json.loads(a.args)
            except Exception as e:
                print("args JSON inválido:", e); sys.exit(4)
            resp = tools_call(p, a.tool, call_args)
            print(f"\n=== tools/call {a.tool} ===")
            print(json.dumps(resp, indent=2, ensure_ascii=False))

        print("\n✅ OK.")
    finally:
        try: p.terminate()
        except Exception: pass

if __name__ == "__main__":
    main()
