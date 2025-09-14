import re, json

def parse_plan_strict(plan_raw: str) -> dict:
    # elimina fences y puntos suspensivos que a veces aparecen
    s = plan_raw.strip()
    s = s.replace("```json", "").replace("```JSON", "").replace("```", "")
    s = s.replace("\u200b", "")  # zero-width chars
    # elimina líneas que sólo tengan "..."
    s = "\n".join([ln for ln in s.splitlines() if ln.strip() != "..."])

    # intenta parse directo
    try:
        return json.loads(s)
    except Exception:
        pass

    # si trae texto extra, extrae el primer bloque que parece JSON
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        candidate = m.group(0)
        # quita comas colgantes comunes
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        return json.loads(candidate)

    # si aún falla, levanta excepción para manejar arriba
    raise ValueError("No JSON object found")
