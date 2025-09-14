# app.py
import os, json, asyncio, time
import httpx
import streamlit as st
from dotenv import load_dotenv
from helpers import parse_plan_strict
from mcp_client import bootstrap_clients

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

PLANNER_SYS = """Eres un router de herramientas MCP. Devuelve SOLO JSON válido.
Dado el mensaje del usuario, selecciona UNA tool con argumentos.
Formato:
{
  "server": "local|remote",
  "tool": "nombre_de_la_tool",
  "arguments": { ... },
  "justification": "breve razonamiento"
}
REGLAS: 
Si el usuario pide analizar una orden -> usa local.orders.analyze con {"order_id": <id>}
Si pide marcar pagada -> usa local.webhooks.order_paid con {"order_id": <id>, "secret": ENV_SECRET}
Si pide transformar/enviar mocks -> usa local.orders.transform / local.orders.send_mock
Si pide validar email/NIT/dirección -> usa remote.validate.email / remote.validate.vat / remote.validate.address
Si no estás seguro, sugiere una pregunta de clarificación devolviendo:
{ "server": null, "tool": null, "arguments": {}, "justification": "..." }

Si pides validar email -> usa remote.validate.email con {"email":"<correo>"} (NO uses "value")
Si pides validar NIT -> usa remote.validate.vat con {"vat":"<nit>"} (NO uses "value")
Si pides validar dirección -> usa remote.validate.address con {"address":"<texto>"} (NO uses "value")
FORMATO ESTRICTO:
- Devuelve SOLO un JSON válido (RFC 8259), minificado, sin backticks, sin comentarios, sin texto antes o después.
- Nada de ``` ni ... ni explicaciones.
- Si usas webhooks.order_paid incluye "secret": "{MCP_WEBHOOK_SECRET}" en arguments.

"""

def build_user_prompt(user_text: str):
    return f"""{PLANNER_SYS}

Usuario:
{user_text}

IMPORTANTE:
- Responde SOLO el JSON del plan. Nada de texto extra.
- Si usas webhooks.order_paid debes incluir "secret": "{MCP_WEBHOOK_SECRET}" en arguments.
"""

# ---------- Streamlit UI ----------
st.set_page_config(page_title="MCP Chatbot", page_icon="🤖", layout="wide")
st.title("🤖 MCP Chatbot (Streamlit + Ollama)")

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

    # 2) Parse plan
    plan = {}
    try:
        plan = parse_plan_strict(plan_raw)

    except Exception:
        st.error("El plan no es JSON válido. Ajusta el prompt o verifica el modelo.")
        st.stop()

    server = plan.get("server")
    tool = plan.get("tool")
    arguments = plan.get("arguments", {})
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
