# MCP Chatbot (Streamlit + MCP stdio/http + Ollama)

A lightweight chatbot UI that routes user requests to **Model Context Protocol (MCP)** servers (both **stdio** and **HTTP**) and uses **Ollama** for planning. It lists available tools, builds a compact planning prompt dynamically, validates and auto-fixes tool/server pairs, and executes tools with clear, structured outputs.

---

## Features

- **Dynamic MCP discovery**
  - HTTP servers via `MCP_HTTP` (e.g., your FastAPI orders server, a remote validator).
  - STDIO servers via `MCP_STDIO` (e.g., official Filesystem MCP, Git MCP).
- **Planner with Ollama**
  - Compact prompt, few-shots, and forced JSON output.
  - Guards to prevent tool/server mixing (e.g., `git_*` → `git`, `validate.*` → `remote`).
- **Safety & normalization**
  - Git arguments normalized (`repo_path` resolved to your repo root).
  - FS arguments normalized to allowed directories.
- **Streamlit UI**
  - Side panel to initialize MCP clients and inspect detected tools.
  - Chat flow with execution previews and JSON results.
- **Works offline for stdio servers and local Ollama**.

---

## Architecture (brief)

```
Streamlit (UI)
  ├─ app.py        → chat loop, planner call, MCP invocation
  ├─ mcp_client_unified.py
  │    ├─ HTTPMCPClient (HTTP JSON-RPC)
  │    └─ StdioMCPClient (stdio; proper notifications/initialized)
  ├─ planner_prompt.py  → compact prompt builder + few-shots
  ├─ helpers.py         → argument normalization, repo detection, guards
  └─ .env               → configuration (Ollama, MCP servers, etc.)
```

---

## Requirements

- Python 3.10+ (tested with 3.13), `virtualenv` recommended.
- **Ollama** running locally (defaults: `127.0.0.1:11434`) with a model (e.g. `llama3.1`).
- Optional MCP servers:
  - **Filesystem MCP** (Node): `@modelcontextprotocol/server-filesystem`
  - **Git MCP** (Python): `mcp-server-git`
- Git installed (for Git MCP and repo detection).

---

## Installation

```bash
# clone and enter the project
git clone <your-repo-url>
cd mcp-client

# create and activate venv
python -m venv venv
source venv/bin/activate

# install python deps
pip install -r requirements.txt
# or: pip install streamlit httpx python-dotenv

# (optional) install Git MCP in this venv
pip install mcp-server-git

# (optional) install Filesystem MCP (Node)
# requires Node/npm
npm i -g @modelcontextprotocol/server-filesystem
# or run with npx (no global install)
```

---

## Configuration

Create a `.env` file in the project root. Example:

```dotenv
# MCP HTTP servers (name:url pairs, comma-separated)
MCP_HTTP="remote:https://your-remote-mcp.example.com/mcp,local:http://localhost:8080/mcp"

# MCP STDIO servers (name:command pairs, comma-separated)
# Use absolute paths; avoid tools not in PATH (e.g. 'uvx' if not installed).
MCP_STDIO="fs:npx -y @modelcontextprotocol/server-filesystem .,git:$(pwd)/venv/bin/python -m mcp_server_git --repository $(pwd)"

# Servers that need the 'notifications/initialized' notification (no id)
MCP_INIT_NOTIFY=git,github

# (Optional) negotiate protocol; defaults to 2024-09 in code
MCP_PROTOCOL=2025-06-18

# Planner / Ollama
OLLAMA_HOST=127.0.0.1
OLLAMA_PORT=11434
OLLAMA_MODEL=llama3.1

# App-specific
MCP_WEBHOOK_SECRET=supersecreto

# (Optional) force absolute git repo root (otherwise auto-detected with git rev-parse)
# GIT_REPO_ABS=/absolute/path/to/your/repo
```

> **Tip (macOS):** If `npx` or `python` aren’t found when launching from Streamlit, use **absolute paths** in `MCP_STDIO`.

---

## Running

1. **Start Ollama** and ensure the model is available:
   ```bash
   ollama pull llama3.1
   ```
2. **Launch the app**:
   ```bash
   source venv/bin/activate
   streamlit run app.py
   ```
3. In the sidebar, click **“Inicializar conexiones”** to connect to MCP servers.  
   The UI will list detected tools under each server.

---

## Usage Examples

Type in the chat (Spanish examples):

- **Filesystem (fs)**  
  - “lista el directorio actual”  
  - “crea un archivo hola.txt con el contenido ‘hola’”
  - “lee el archivo README.md”

- **Git (git)**  
  - “muéstrame el estado” → uses `git_status` with `repo_path:"."`  
  - “agrega hola.txt al stage” → uses `git_add` with `files:["hola.txt"]`  
  - “haz commit con mensaje ‘feat: saludo’” → `git_commit` with `message`  
  - “muestra el HEAD actual” → `git_show` with `revision:"HEAD"`

- **Remote validation (remote)**  
  - “valida el mail juan@acme.com” → `validate.email`  
  - “valida NIT 1234…” → `validate.vat`

- **Local orders (local)**  
  - “analiza la orden 1” → `orders.analyze`  
  - “marca pagada la 2” → `webhooks.order_paid` (includes `secret` automatically via prompt rules)

> The planner returns a compact JSON plan; the client normalizes arguments (e.g., resolves `repo_path` to your repo root) and fixes server/tool mismatches before executing.

---

## Key Behaviors & Guards

- **Forced JSON from planner**: The app requests `format:"json"` and `temperature:0` for planning.
- **Compact prompt**: The planner prompt only includes tool names and required fields, plus few-shots.
- **Server/tool guard**: Heuristic auto-fix (e.g., any `git_*` tool will run on `git` server; `validate.*` on `remote`; `orders.*`/`webhooks.*` on `local`).
- **Notifications for stdio**: For `git` MCP, the app sends `notifications/initialized` (no `id`) after `initialize` and **does not** wait for a response to avoid stream desync.

---

## Troubleshooting

- **`tools/list` returns `-32602 Invalid request parameters`**  
  Ensure `notifications/initialized` is sent (no `id`) before listing tools for servers that need it (e.g., add `git` to `MCP_INIT_NOTIFY`).

- **`uvx` not found**  
  Don’t use `uvx` in `MCP_STDIO` unless it’s in PATH. Prefer the absolute Python from your venv:
  ```
  MCP_STDIO="git:/abs/path/venv/bin/python -m mcp_server_git --repository /abs/path/repo"
  ```

- **Filesystem access denied**  
  The Filesystem MCP restricts access to the working directory. Use relative paths inside the project or adjust the command’s allowed root.

- **Planner outputs free text**  
  The app forces JSON format; if you change this, keep a repair pass or re-enable `format:"json"` and low temperature.

- **Git repo path wrong**  
  The client normalizes `repo_path:"."` to the repo root via `git rev-parse`. You can override with `GIT_REPO_ABS`.

---

## Development

- Update the planner rules/few-shots in `planner_prompt.py`.
- Add/modify argument normalizers and guards in `helpers.py`.
- Extend MCP clients in `mcp_client_unified.py` (HTTP/STDIO).
- Keep `.gitignore` (Python caches):
  ```
  __pycache__/
  *.py[cod]
  *$py.class
  ```

---

## Security Notes

- Secrets like `MCP_WEBHOOK_SECRET` are injected only when needed (e.g., `local.webhooks.order_paid`).
- Prefer HTTPS for remote MCP servers.
- Be cautious with `write_file`/`edit_file` tools; the Filesystem MCP enforces an allowed root.

