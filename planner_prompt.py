# planner_prompt.py
import json

BASE_RULES = """Eres un router de herramientas MCP. Devuelve SOLO JSON válido.
Tu salida DEBE tener el formato:
{
  "server": "<nombre_server> ",
- SERVERS VÁLIDOS: usa solo {"fs","git","local","remote"}.
  "tool": "<nombre_tool>",
  "arguments": { ... },
  "justification": "breve razonamiento"
}
Si ninguna tool aplica, devuelve:
{ "server": null, "tool": null, "arguments": {}, "justification": "explica por qué y pide aclaración" }
"""

def tools_catalog_block(clients) -> str:
    """
    Construye un bloque textual con todas las tools disponibles:
    - server
    - nombre de tool
    - descripción
    - schema de entrada (para que el LLM arme los arguments correctos)
    """
    lines = ["Herramientas disponibles:\n"]
    for sname, cli in clients.items():
        for t in cli.tools:
            lines.append(f"- server: {sname}")
            lines.append(f"  tool: {t.get('name')}")
           
            schema = t.get("inputSchema") or {}
            # Recorta el schema si es gigante
            schema_json = json.dumps(schema, ensure_ascii=False)
            if len(schema_json) > 900:
                schema_json = schema_json[:900] + "…"
            lines.append(f"  inputSchema: {schema_json}")
            lines.append("")  # espacio
    return "\n".join(lines)

def build_dynamic_planner_prompt(clients, extra_rules: str = "") -> str:
    """
    Devuelve un prompt 'sistema' para el planner que incluye:
    - reglas base
    - (opcional) reglas tuyas
    - catálogo de tools disponibles
    """
    parts = [BASE_RULES]
    if extra_rules:
        parts.append("\nReglas específicas:\n" + extra_rules.strip() + "\n")
    parts.append(tools_catalog_block(clients))
    parts.append("\nIMPORTANTE:\n- Responde SOLO con el JSON del plan.")
    return "\n".join(parts)
