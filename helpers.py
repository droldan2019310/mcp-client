# helpers.py 
import json, re
from typing import Tuple, Optional
FS_PATH_KEYS = {"path", "source", "destination"}  
from pathlib import Path
from typing import Dict, Any, List
import os, subprocess

class PlanParseError(Exception):
    def __init__(self, message: str, *, raw: str, cleaned: str, candidate: Optional[str], last_error: Optional[Exception]):
        super().__init__(message)
        self.raw = raw
        self.cleaned = cleaned
        self.candidate = candidate
        self.last_error = last_error

def parse_plan_strict(plan_raw: str, *, return_debug: bool = False) -> dict | Tuple[dict, dict]:
    """
    Intenta extraer un JSON {..} del texto del modelo.
    En caso de error, lanza PlanParseError con campos para depurar.
    Si return_debug=True, devuelve (plan, debug) donde debug incluye raw/cleaned/candidate.
    """
    debug = {"raw": plan_raw, "cleaned": "", "candidate": None, "errors": []}

    # 1) limpiar fences, ZWSP y líneas '...'
    s = plan_raw.strip()
    s = s.replace("```json", "").replace("```JSON", "").replace("```", "")
    s = s.replace("\u200b", "")
    s = "\n".join([ln for ln in s.splitlines() if ln.strip() != "..."])
    debug["cleaned"] = s

    # helper para quitar comas colgantes comunes antes de cerrar } o ]
    def _fix_trailing_commas(txt: str) -> str:
        txt = re.sub(r",\s*([}\]])", r"\1", txt)
        return txt

    # 2) intento directo
    try:
        plan = json.loads(s)
        return (plan, debug) if return_debug else plan
    except Exception as e:
        debug["errors"].append(f"direct: {repr(e)}")
        last_error = e

    # 3) extraer el primer bloque {...} si hay texto extra
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        candidate = m.group(0)
        candidate = _fix_trailing_commas(candidate)
        debug["candidate"] = candidate
        try:
            plan = json.loads(candidate)
            return (plan, debug) if return_debug else plan
        except Exception as e:
            debug["errors"].append(f"candidate: {repr(e)}")
            last_error = e
    else:
        candidate = None

    # 4) levantar error con contexto completo
    raise PlanParseError(
        "No JSON object found",
        raw=plan_raw,
        cleaned=s,
        candidate=candidate,
        last_error=last_error
    )



FS_PATH_KEYS = {"path", "source", "destination"}

def _normalize_path_into_base(base_dir: str, value: str) -> str:
    base = Path(base_dir).resolve()
    raw = Path(str(value).lstrip("/"))         # quita slash inicial si lo trae
    target = (base / raw).resolve()
    try:
        target.relative_to(base)               # ¿sigue dentro del base?
    except ValueError:
        target = (base / raw.name).resolve()   # fuera → usar basename dentro de base
    return str(target)

def fs_normalize_args(args: Dict[str, Any], base_dir: str | None) -> Dict[str, Any]:
    """Devuelve args con rutas normalizadas dentro de base_dir (si se provee)."""
    if not base_dir or not isinstance(args, dict):
        return args
    fixed = dict(args)
    for k, v in list(fixed.items()):
        if k in FS_PATH_KEYS and isinstance(v, str):
            fixed[k] = _normalize_path_into_base(base_dir, v)
        elif k == "paths" and isinstance(v, list):
            fixed[k] = [
                _normalize_path_into_base(base_dir, p) if isinstance(p, str) else p
                for p in v
            ]
    return fixed



def detect_repo_root(fallback: str | None = None) -> str:
    """
    Devuelve la raíz ABSOLUTA del repo git actual.
    Si falla, usa fallback o cwd.
    """
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.STDOUT,
            text=True,
            cwd=os.getcwd(),
        ).strip()
        if out:
            return out
    except Exception:
        pass
    return fallback or os.getcwd()

def normalize_git_args(arguments: dict, repo_abs: str) -> dict:
    """
    - Rellena/normaliza repo_path:
        .  -> repo_abs
        "/path/to/repository" -> repo_abs
        relativo -> absolutiza contra repo_abs
    - files: si viene string -> list
    """
    a = dict(arguments or {})
    rp = a.get("repo_path")

    if not rp or rp == "." or rp == "/path/to/repository":
        a["repo_path"] = repo_abs
    else:
        # si no es absoluta, hazla absoluta respecto al repo
        if not os.path.isabs(rp):
            a["repo_path"] = os.path.abspath(os.path.join(repo_abs, rp))

    if "files" in a and isinstance(a["files"], str):
        a["files"] = [a["files"]]

    return a
