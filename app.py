# app.py
import os, json, asyncio, time
import httpx
import streamlit as st
from dotenv import load_dotenv
from helpers import parse_plan_strict, PlanParseError, fs_normalize_args
from mcp_client import bootstrap_clients
from helpers import detect_repo_root, normalize_git_args



from mcp_config_loader import load_mcp_config, build_clients_from_config
from planner_prompt import build_dynamic_planner_prompt


load_dotenv()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1")
OLLAMA_PORT = os.getenv("OLLAMA_PORT", "11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
MCP_WEBHOOK_SECRET = os.getenv("MCP_WEBHOOK_SECRET", "changeme")

# ---------- Ollama wrapper ----------
def build_chat_prompt(history: list[dict], user_text: str) -> str:
    """
    Convierte el historial en un prompt estilo chat para una respuesta general.
    history: [{role: user|assistant, content: str}, ...]
    """
    sys = (
        "Eres un asistente útil. Responde de forma clara y concisa en español.\n"
        "Si el usuario pide algo que requiere Tools MCP (validar email/NIT/dirección "
        "o analizar/procesar órdenes), sugiere hacerlo y explica brevemente cómo.\n"
    )
    lines = [f"<system>{sys}</system>"]
    for m in history[-12:]:
        role = m["role"]
        content = m["content"]
        lines.append(f"<{role}>{content}</{role}>")
    lines.append(f"<user>{user_text}</user>\n<assistant>")
    return "\n".join(lines)

async def general_answer(history: list[dict], user_text: str) -> str:
    prompt = build_chat_prompt(history, user_text)
    return await ollama_complete(prompt)

async def ollama_complete(prompt: str, model: str = None) -> str:
    mdl = model or OLLAMA_MODEL
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate"
    payload = {"model": mdl, "prompt": prompt, "stream": False}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    return data.get("response", "")


if "clients" not in st.session_state:
    st.session_state.clients = None
if "tools_index" not in st.session_state:
    st.session_state.tools_index = {}
if "planner_system" not in st.session_state:
    st.session_state.planner_system = None
if "fs_bases" not in st.session_state:
    st.session_state.fs_bases = {}  



def build_user_prompt(user_text: str):
    # Usa el planner dinámico 
    sys = st.session_state.planner_system or "Eres un router MCP. Devuelve JSON."
    return f"""{sys}

Usuario:
{user_text}

IMPORTANTE:
- Responde SOLO el JSON del plan. Nada de texto extra.
- Si usas webhooks.order_paid debes incluir "secret": "{MCP_WEBHOOK_SECRET}" en arguments.
"""

# ---------- Streamlit UI ----------
st.set_page_config(page_title="MCP Chatbot", page_icon="🤖", layout="wide")
st.title("🤖 MCP Chatbot")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "clients" not in st.session_state:
    st.session_state.clients = None
if "tools_index" not in st.session_state:
    st.session_state.tools_index = {}

with st.sidebar:
    st.header("Conexiones MCP")
    local_url = os.getenv("MCP_LOCAL_URL", "http://localhost:8080/mcp")
    remote_url = os.getenv("MCP_REMOTE_URL", "")
    st.write(f"Local: `{local_url}`")
    st.write(f"Remoto: `{remote_url or '—'}`")
    if st.button("Inicializar conexiones"):
        with st.spinner("Inicializando MCP clients..."):
            st.session_state.clients = asyncio.run(bootstrap_clients())
            # Construye índice de tools por server:name
            idx = {}
            for sname, cli in st.session_state.clients.items():
                for t in cli.tools:
                    idx[f"{sname}:{t['name']}"] = t
            st.session_state.tools_index = idx
        st.success("Conectado.")

    st.divider()
    st.caption("Modelo Ollama")
    st.write(os.getenv("OLLAMA_MODEL", "llama3.1"))
    st.divider()
    st.caption("Config MCP dinámica")
    cfg_path = st.text_input("Ruta config JSON", value="mcp.config.json")
    
    
    if st.session_state.clients:
            # Construye y guarda el prompt DINÁMICO del planner (con catálogo real)
        st.session_state.planner_system = build_dynamic_planner_prompt(
            st.session_state.clients,
            extra_rules="""
            - Devuelve SIEMPRE un JSON minificado válido (RFC8259).
            - PROHIBIDO texto libre, disculpas o notas. Nada de ``` ni markdown.
            - Si hay duda, usa plan nulo EXACTO:
            {"server": null, "tool": null, "arguments": {}, "justification": "uncertain"}
            - No inventes tools ni argumentos fuera del inputSchema listado.
            - Para fs.* usa rutas RELATIVAS; si el usuario da una ruta absoluta, usa solo su nombre dentro del directorio permitido.
            - Si no conoces el path base o el usuario no dio ruta, primero llama fs.list_allowed_directories y luego opera dentro de la primera ruta permitida.
            - Para fs.*, usa rutas RELATIVAS (sin '/' inicial). Si el usuario da una absoluta, usa solo el nombre dentro de la base permitida.
            - Para todas las tools git, usa "repo_path":"." (NUNCA "/path/to/repository").
            - NUNCA mezcles server y tool: la tool debe existir en el MISMO server elegido.
            - Si la tool empieza con "git_" → usa server "git".
            - Si la tool tiene  "file" → usa server "fs".
            - Si la tool es "validate.email" / "validate.vat" / "validate.address" → usa server "remote".
            - Si la tool empieza con "orders." o "webhooks." → usa server "local".
            - Si la intención es validar email/NIT/dirección → usa "remote" + una de las validate.* (no git_*).
            - Si la intención es Git (status, add, commit, checkout, show, log, diff, etc.) → usa server "git" + tool git_*.
            - "repo_path" por defecto en todas las tools git: "." (NUNCA "/path/to/repo" ni placeholders).
            - Devuelve SIEMPRE un JSON minificado válido (RFC8259). Si hay duda, plan nulo EXACTO:
            {"server": null, "tool": null, "arguments": {}, "justification": "uncertain"}
            - Si el usuario no da ruta, usa {"repo_path": "."} y deja que el cliente la normalice a la raíz del repo.
            - Extrae nombres de archivos directamente del texto del usuario (ej: "hola.txt"); si dice "todos" o "todo", usa {"files": ["."]}.
            - Responde SIEMPRE con JSON válido minificado. Si hay duda, devuelve el plan nulo EXACTO:
            {"server": null, "tool": null, "arguments": {}, "justification": "uncertain"}
                    """.strip()
                )

        with st.expander("⚙️ Ver prompt del planner (system)"):
            st.code(st.session_state.planner_system, language="markdown")
            st.divider()
        st.subheader("Tools detectadas")
        for sname, cli in st.session_state.clients.items():
            st.markdown(f"**Server:** `{sname}`")
            for t in cli.tools:
                st.write(f"- `{t['name']}` — {t.get('description','')}")


# historial
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

user_text = st.chat_input("Escribe tu mensaje… (e.g., 'analiza la orden 1', 'marca pagada la 2', 'valida email juan@ejemplo.com')")
if user_text:
    st.session_state.messages.append({"role": "user", "content": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    # 1) Plan con Ollama
    with st.chat_message("assistant"):
        with st.spinner("Pensando plan con Ollama…"):
            plan_raw = asyncio.run(ollama_complete(build_user_prompt(user_text)))

    plan = {}
    try:
        plan, dbg = parse_plan_strict(plan_raw, return_debug=True)
    except PlanParseError as e:
        st.error("El plan no es JSON válido. Ajusta el prompt o verifica el modelo.")
        with st.expander("Ver depuración del plan (raw/cleaned/candidate)"):
            st.markdown("**Raw (respuesta del modelo):**")
            st.code(e.raw or "", language="json")

            st.markdown("**Cleaned (tras limpieza):**")
            st.code(e.cleaned or "", language="json")

            if e.candidate:
                st.markdown("**Candidate (bloque detectado):**")
                st.code(e.candidate, language="json")

            if e.last_error:
                st.markdown("**Último error de json.loads():**")
                st.code(repr(e.last_error))

        st.stop()
    except Exception as e:
        st.error(f"Fallo inesperado al parsear el plan: {e}")
        with st.expander("Trace rápido"):
            st.code(repr(e))
        st.stop()

    server = plan.get("server")
    tool = plan.get("tool")
    arguments = plan.get("arguments", {})
    base_dir = (st.session_state.fs_bases or {}).get(server)
    arguments = fs_normalize_args(arguments, base_dir)

    if "repo_abs" not in st.session_state:
        st.session_state.repo_abs = os.getenv("GIT_REPO_ABS", detect_repo_root())

    # Normaliza si es tool de git
    if server == "git" and tool and tool.startswith("git_"):
        arguments = normalize_git_args(arguments, st.session_state.repo_abs)

    justification = plan.get("justification")

    if not server or not tool:
        # FALLBACK: chat general con Ollama
        with st.chat_message("assistant"):
            with st.spinner("Respuesta directa…"):
                answer = asyncio.run(general_answer(st.session_state.messages, user_text))
            st.session_state.messages.append({"role": "assistant", "content": answer})
            st.markdown(answer)
        st.stop()

    with st.chat_message("assistant"):
        st.code(json.dumps(plan, ensure_ascii=False, indent=2), language="json")

    # 3) Ejecutar tool MCP
    clients = st.session_state.clients
    if not clients or server not in clients:
        st.error(f"Server MCP '{server}' no inicializado. Usa el botón en la barra lateral.")
        st.stop()

    cli = clients[server]

    # Para analyze: permitir prompt opcional
    if tool == "orders.analyze" and "prompt" not in arguments:
        # puedes pasarle el texto completo como prompt enriquecido:
        arguments["prompt"] = "Analiza coherencia de totales y campos críticos. " + user_text

    with st.chat_message("assistant"):
        st.markdown(f"**Tool**: `{server}.{tool}`")
        st.json(arguments)

        with st.spinner("Llamando herramienta…"):
            try:
                resp = asyncio.run(cli.call_tool(tool, arguments))
            except httpx.HTTPError as e:
                st.error(f"HTTP error: {e}")
                st.stop()

        # 4) Mostrar respuesta MCP
        result = resp.get("result") or resp.get("error")
        st.json(result)

        # 5) Guardar en historial chat
        out_text = f"**{server}.{tool}** → `{('ok' if isinstance(result, dict) and result.get('ok') else 'done')}`"
        if isinstance(result, dict) and "analysis" in result:
            out_text += f"\n\n**Análisis**:\n{result['analysis']}"
        st.session_state.messages.append({"role": "assistant", "content": out_text})
        st.markdown(out_text)
