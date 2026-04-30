"""
@file server.py
@brief Flask front-end for the Build123d sandbox (bare-metal adaptation).

Phase 6 — pool de workers persistants :
    Un pool de processus `worker.py --persistent` évite de réimporter
    build123d (~2–4 s) à chaque requête. Isolation subprocess + RLIMIT_AS
    inchangées (par worker). Timeout : WORKER_REQUEST_TIMEOUT (fallback
    CADQUERY_EXEC_TIMEOUT).
"""

from __future__ import annotations

import atexit
import io
import json
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from pool import WorkerPool

try:
    from dotenv import load_dotenv

    repo_root_env = Path(__file__).resolve().parent.parent / ".env"
    if repo_root_env.is_file():
        load_dotenv(repo_root_env)
except ImportError:
    pass


app = Flask(__name__)

# --- Pool --------------------------------------------------------------------

_POOL_SIZE = int(os.environ.get("WORKER_POOL_SIZE", "2"))
_WORKER_MEM_MB = int(os.environ.get("CADQUERY_WORKER_MEM_LIMIT_MB", "4096"))
_REQUEST_TIMEOUT = float(
    os.environ.get(
        "WORKER_REQUEST_TIMEOUT",
        os.environ.get("CADQUERY_EXEC_TIMEOUT", "30"),
    )
)

pool = WorkerPool(size=_POOL_SIZE, mem_limit_mb=_WORKER_MEM_MB)
pool.start()
atexit.register(pool.shutdown)


def _worker_error_message(resp: dict) -> str:
    msg = str(resp.get("error") or "Unknown worker error")
    tb = (resp.get("traceback") or "").strip()
    if tb:
        return msg + "\n" + tb
    return msg


def make_response(data=None, message="Success", status=200):
    """@brief Generic JSON response helper (matches phases 1–4)."""
    return (
        json.dumps({"data": data if data else "None", "message": message}),
        status,
    )


# --- Routes ------------------------------------------------------------------


@app.route("/health", methods=["GET"])
def health():
    """
    @brief Liveness probe used by start.sh's watchdog.

    Répond en JSON léger (pas d'exécution utilisateur). Phase 6 : état du pool.
    """
    return jsonify(
        {
            "status": "ok",
            "workers_alive": pool.workers_alive(),
            "workers_total": pool.workers_total(),
        }
    )


@app.route("/preview", methods=["POST"])
def run_preview():
    try:
        code = request.json["code"]
        resp = pool.execute("preview", code, timeout=_REQUEST_TIMEOUT)
        if not resp.get("ok"):
            return make_response(message=_worker_error_message(resp), status=400)
        mesh_data = {
            "vertices": resp["vertices"],
            "faces": resp["faces"],
            "objectCount": resp["objectCount"],
        }
        return make_response(data=mesh_data, message="Preview generated successfully")
    except Exception as e:
        return make_response(message=str(e), status=500)


def _send_export(code: str, mode: str, download_name: str):
    """Common /stl + /step handler: run worker pool, stream from memory."""
    resp = pool.execute(mode, code, timeout=_REQUEST_TIMEOUT)
    if not resp.get("ok"):
        return make_response(message=_worker_error_message(resp), status=400)
    output_path = resp.get("path")
    if not output_path or not isinstance(output_path, str):
        return make_response(message="Worker did not return output path", status=400)
    try:
        with open(output_path, "rb") as fh:
            payload = fh.read()
    finally:
        try:
            os.unlink(output_path)
        except OSError:
            pass
    return send_file(
        io.BytesIO(payload),
        as_attachment=True,
        download_name=download_name,
        mimetype="application/octet-stream",
    )


@app.route("/stl", methods=["POST"])
def run_stl():
    try:
        code = request.json["code"]
        return _send_export(code, "stl", "model.stl")
    except Exception as e:
        return make_response(message=str(e), status=500)


@app.route("/step", methods=["POST"])
def run_step():
    try:
        code = request.json["code"]
        return _send_export(code, "step", "model.step")
    except Exception as e:
        return make_response(message=str(e), status=500)


if __name__ == "__main__":
    host = os.environ.get("CADQUERY_HOST", "127.0.0.1")
    port = int(os.environ.get("CADQUERY_PORT", "5002"))
    print(f"Build123d server starting on http://{host}:{port}")
    print(
        f"[server] worker pool: size={_POOL_SIZE}, "
        f"request_timeout={_REQUEST_TIMEOUT:g}s, mem_limit_mb={_WORKER_MEM_MB}"
    )
    app.run(host=host, port=port, threaded=True)
