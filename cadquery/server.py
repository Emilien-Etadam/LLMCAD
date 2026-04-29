"""
@file server.py
@brief Process CadQuery requests and send using Flask
@author 30hours (bare-metal adaptation)
"""

import builtins as _builtins
import json
import math
import os
import resource
import tempfile
import threading
from pathlib import Path

import cadquery as cq
import numpy as np
from flask import Flask, request, send_file

from CadQueryValidator import CadQueryValidator
from Preview import preview

try:
    from dotenv import load_dotenv

    repo_root_env = Path(__file__).resolve().parent.parent / ".env"
    if repo_root_env.is_file():
        load_dotenv(repo_root_env)
except ImportError:
    pass

app = Flask(__name__)
validator = CadQueryValidator()


# --- Sandbox configuration ---------------------------------------------------

# Hard wall-clock cap on a single /preview|/stl|/step execution.
EXEC_TIMEOUT_SEC = float(os.environ.get("CADQUERY_EXEC_TIMEOUT", "30"))

# Optional virtual-address-space cap (RLIMIT_AS), in MB. Disabled by default
# because CadQuery + cadquery-ocp + numpy easily reserve >1 GB of VA at idle
# (see STATUS.md phase 3.5). Operators on a constrained LXC can override via
# `CADQUERY_MEM_LIMIT_MB=512 ./start.sh`.
MEM_LIMIT_MB = int(os.environ.get("CADQUERY_MEM_LIMIT_MB", "0"))

# Modules importable from inside the sandbox. Mirrors the validator whitelist.
_RUNTIME_ALLOWED_IMPORTS = {"cadquery", "math", "numpy", "typing"}


def _sandbox_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Restricted __import__: only modules on the runtime whitelist."""
    if level != 0:
        raise ImportError("Relative imports are not allowed in sandbox")
    root = name.split(".")[0]
    if root not in _RUNTIME_ALLOWED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed in sandbox")
    return __import__(name, globals, locals, fromlist, level)


def _build_safe_builtins():
    safe = {
        name: getattr(_builtins, name)
        for name in validator.allowed_builtins
        if hasattr(_builtins, name)
    }
    # Restricted import so that `import math` / `from typing import List`
    # work inside user code without exposing arbitrary modules.
    safe["__import__"] = _sandbox_import
    return safe


def _apply_memory_limit():
    """Best-effort RLIMIT_AS cap. Logs (and continues) on failure."""
    if MEM_LIMIT_MB <= 0:
        return
    try:
        target = MEM_LIMIT_MB * 1024 * 1024
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            new_hard = min(target, hard)
        else:
            new_hard = target
        new_soft = min(target, new_hard)
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, new_hard))
        print(f"[server] RLIMIT_AS set to {MEM_LIMIT_MB} MB")
    except (ValueError, OSError) as exc:
        print(
            f"[server] Could not set RLIMIT_AS to {MEM_LIMIT_MB} MB "
            f"(LXC restriction? {exc}); continuing without memory cap."
        )


_apply_memory_limit()


# --- Execution --------------------------------------------------------------


def execute(code):
    """
    @brief All remote code execution goes through this function.
      - Validates the code via CadQueryValidator
      - Executes inside a restricted-builtins sandbox in a worker thread
      - Aborts (returns an error) if the worker exceeds EXEC_TIMEOUT_SEC
    """
    cleaned_code, error = validator.validate(code)
    if error:
        return None, error

    safe_builtins = _build_safe_builtins()
    globals_dict = {
        "cq": cq,
        "np": np,
        "math": math,
        "__builtins__": safe_builtins,
    }
    locals_dict = {}

    state = {"error": None}

    def runner():
        try:
            exec(cleaned_code, globals_dict, locals_dict)
        except BaseException as exc:  # noqa: BLE001 - report any user-code failure
            state["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=runner, daemon=True)
    worker.start()
    worker.join(EXEC_TIMEOUT_SEC)
    if worker.is_alive():
        # We cannot forcibly kill a CPython thread, but we surface the timeout
        # to the caller; the daemon thread will be torn down with the process.
        return None, (
            f"Execution timeout exceeded ({EXEC_TIMEOUT_SEC:g}s). "
            "Possible infinite loop or runaway computation."
        )
    if state["error"] is not None:
        return None, state["error"]
    return locals_dict, None


def make_response(data=None, message="Success", status=200):
    """
    @brief Generic function to send HTTP responses
    """
    return (
        json.dumps({"data": data if data else "None", "message": message}),
        status,
    )


@app.route("/preview", methods=["POST"])
def run_preview():
    try:
        code = request.json["code"]
        output, error = execute(code)
        if error:
            return make_response(message=error, status=400)
        mesh_data, error = preview(output["result"])
        if error:
            return make_response(message=error, status=400)
        return make_response(data=mesh_data, message="Preview generated successfully")
    except Exception as e:
        return make_response(message=str(e), status=500)


@app.route("/stl", methods=["POST"])
def run_stl():
    try:
        code = request.json["code"]
        result, error = execute(code)
        if error:
            return make_response(message=error, status=400)
        model = result["result"]
        temp_file = tempfile.NamedTemporaryFile(suffix=".stl", delete=True)
        cq.exporters.export(model, temp_file.name)
        response = send_file(
            temp_file.name,
            as_attachment=True,
            download_name="model.stl",
            mimetype="application/octet-stream",
        )

        @response.call_on_close
        def cleanup():
            temp_file.close()

        return response
    except Exception as e:
        return make_response(message=str(e), status=500)


@app.route("/step", methods=["POST"])
def run_step():
    try:
        code = request.json["code"]
        result, error = execute(code)
        if error:
            return make_response(message=error, status=400)
        model = result["result"]
        temp_file = tempfile.NamedTemporaryFile(suffix=".step", delete=True)
        cq.exporters.export(model, temp_file.name)
        response = send_file(
            temp_file.name,
            as_attachment=True,
            download_name="model.step",
            mimetype="application/octet-stream",
        )

        @response.call_on_close
        def cleanup():
            temp_file.close()

        return response
    except Exception as e:
        return make_response(message=str(e), status=500)


if __name__ == "__main__":
    host = os.environ.get("CADQUERY_HOST", "127.0.0.1")
    port = int(os.environ.get("CADQUERY_PORT", "5002"))
    print(f"CadQuery server starting on http://{host}:{port}")
    print(
        f"[server] sandbox: exec_timeout={EXEC_TIMEOUT_SEC:g}s, "
        f"mem_limit_mb={MEM_LIMIT_MB or 'disabled'}, "
        f"runtime_imports={sorted(_RUNTIME_ALLOWED_IMPORTS)}"
    )
    app.run(host=host, port=port)
