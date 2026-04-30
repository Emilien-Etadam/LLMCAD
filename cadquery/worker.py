"""
@file worker.py
@brief Subprocess worker that executes Build123d user scripts.

@usage
    # One-shot (legacy / tests) — supprimé du serveur après phase 6.
    python worker.py <code_file> <output_file> <mode>
      mode in: preview | stl | step

    # Pool persistant (stdin/stdout JSON une ligne par requête)
    python worker.py --persistent

@persistent protocol
    Ligne requête: {"op":"preview"|"stl"|"step","code":"..."}
    Ligne réponse succès preview: {"ok":true,"vertices":[...],"faces":[...],"objectCount":N}
    Ligne réponse succès stl/step: {"ok":true,"path":"/tmp/cqout_xxx.stl"}
    Ligne réponse erreur: {"ok":false,"error":"...","traceback":"..."}

@notes
    RLIMIT_AS avant import build123d. Import build123d une seule fois ;
    chaque requête utilise exec(code, fresh_globals) avec fresh_globals
    = copie du template `from build123d import *`.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import tempfile
import traceback
from typing import Any, Dict

# RLIMIT_AS must be set BEFORE importing the heavy stack (build123d / OCP).
_MEM_LIMIT_MB = int(os.environ.get("CADQUERY_WORKER_MEM_LIMIT_MB", "4096"))


def _apply_memory_limit() -> None:
    """Best-effort RLIMIT_AS cap. Failure is logged but non-fatal."""
    if _MEM_LIMIT_MB <= 0:
        return
    target = _MEM_LIMIT_MB * 1024 * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            new_hard = min(target, hard)
        else:
            new_hard = target
        new_soft = min(target, new_hard)
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, new_hard))
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"[worker] could not set RLIMIT_AS: {exc}\n")


_apply_memory_limit()

import build123d as _b3d  # noqa: E402

from Preview import compute_preview  # noqa: E402

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_VALIDATION = 2

# Template namespace: filled once after imports (symbols from build123d *).
_NS_TEMPLATE: Dict[str, Any] = {}
exec("from build123d import *", _NS_TEMPLATE)  # noqa: S102


def _fresh_globals() -> Dict[str, Any]:
    """Nouveau dict par requête — pas de fuite de variables utilisateur."""
    g = dict(_NS_TEMPLATE)
    g["__name__"] = "__user_script__"
    g["b3d"] = _b3d
    return g


def _to_compound(result: Any) -> _b3d.Compound:
    """Wrap any supported result into a Compound for STEP/STL export."""
    if isinstance(result, _b3d.Compound):
        return result
    if isinstance(result, _b3d.Solid):
        return _b3d.Compound([result])
    raise RuntimeError(f"type non supporté pour export: {type(result).__name__}")


def _do_stl(result: Any, output_path: str) -> None:
    compound = _to_compound(result)
    _b3d.export_stl(compound, output_path, tolerance=0.01, angular_tolerance=0.1)


def _do_step(result: Any, output_path: str) -> None:
    compound = _to_compound(result)
    _b3d.export_step(compound, output_path)


def _do_preview_file(result: Any, output_path: str) -> int:
    payload = compute_preview(result)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return EXIT_OK


def _execute_user_code(code: str) -> tuple[Any | None, Dict[str, Any] | None]:
    """
    Exécute `code` dans un namespace frais. Retourne (result, err_response).
    err_response est non None si échec (dict JSON worker).
    """
    namespace = _fresh_globals()
    try:
        exec(code, namespace)  # noqa: S102
    except BaseException as exc:  # noqa: BLE001
        return None, {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    if "result" not in namespace:
        return None, {
            "ok": False,
            "error": "Code did not assign a value to 'result'",
            "traceback": "",
        }
    return namespace["result"], None


def _process_json_request(req: Dict[str, Any]) -> Dict[str, Any]:
    op = req.get("op")
    code = req.get("code")
    if op not in {"preview", "stl", "step"} or not isinstance(code, str):
        return {
            "ok": False,
            "error": "Invalid request: need op in preview|stl|step and code string",
            "traceback": "",
        }

    result, err = _execute_user_code(code)
    if err is not None:
        return err

    try:
        if op == "preview":
            payload = compute_preview(result)
            out: Dict[str, Any] = {"ok": True}
            out.update(payload)
            return out
        if op == "stl":
            fd, path = tempfile.mkstemp(suffix=".stl", prefix="cqout_")
            os.close(fd)
            try:
                _do_stl(result, path)
            except BaseException:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                raise
            return {"ok": True, "path": path}
        if op == "step":
            fd, path = tempfile.mkstemp(suffix=".step", prefix="cqout_")
            os.close(fd)
            try:
                _do_step(result, path)
            except BaseException:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                raise
            return {"ok": True, "path": path}
    except BaseException as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }

    return {"ok": False, "error": "unreachable", "traceback": ""}


def _write_json_line(obj: Dict[str, Any]) -> None:
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def run_persistent() -> None:
    """Boucle stdin → stdout jusqu'à EOF."""
    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                req = json.loads(raw)
            except json.JSONDecodeError as exc:
                _write_json_line(
                    {
                        "ok": False,
                        "error": f"Invalid JSON: {exc}",
                        "traceback": "",
                    }
                )
                continue
            if not isinstance(req, dict):
                _write_json_line(
                    {
                        "ok": False,
                        "error": "Request must be a JSON object",
                        "traceback": "",
                    }
                )
                continue
            resp = _process_json_request(req)
            _write_json_line(resp)
    except BrokenPipeError:
        pass


def main_argv() -> int:
    """Mode fichier : python worker.py <code_file> <output_file> <mode>"""
    if len(sys.argv) != 4:
        sys.stderr.write(
            "Usage: worker.py <code_file> <output_file> <mode>\n"
            "   or: worker.py --persistent\n"
        )
        return EXIT_VALIDATION

    code_path = sys.argv[1]
    output_path = sys.argv[2]
    mode = sys.argv[3]

    if mode not in {"preview", "stl", "step"}:
        sys.stderr.write(f"Invalid mode '{mode}'\n")
        return EXIT_VALIDATION

    try:
        with open(code_path, "r", encoding="utf-8") as fh:
            code = fh.read()
    except OSError as exc:
        sys.stderr.write(f"Could not read code file '{code_path}': {exc}\n")
        return EXIT_VALIDATION

    result, err = _execute_user_code(code)
    if err is not None:
        sys.stderr.write(err["error"] + "\n")
        if err.get("traceback"):
            sys.stderr.write(err["traceback"])
        return EXIT_RUNTIME

    try:
        if mode == "preview":
            return _do_preview_file(result, output_path)
        if mode == "stl":
            _do_stl(result, output_path)
            return EXIT_OK
        if mode == "step":
            _do_step(result, output_path)
            return EXIT_OK
    except BaseException as exc:  # noqa: BLE001
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        traceback.print_exc(file=sys.stderr)
        return EXIT_RUNTIME

    return EXIT_RUNTIME


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--persistent":
        run_persistent()
    else:
        sys.exit(main_argv())


if __name__ == "__main__":
    main()
