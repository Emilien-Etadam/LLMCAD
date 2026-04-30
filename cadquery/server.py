"""
@file server.py
@brief Flask front-end for the CadQuery sandbox (bare-metal adaptation).

@author 30hours

Phase 4.5 — process isolation:
    Each /preview, /stl, /step request is now executed by a fresh
    `worker.py` subprocess instead of an in-process thread. This means:
      * Wall-clock timeouts are enforced via subprocess.kill, including for
        runaway native (OpenCascade) code that previously kept a Python
        thread alive forever.
      * A segfault inside libTKBO/libTKMath/etc. only kills the worker;
        Flask stays up and the next request goes through.
      * `RLIMIT_AS` can finally be applied — it lives inside worker.py
        (default 2 GiB; configurable via CADQUERY_WORKER_MEM_LIMIT_MB) and
        only caps the user script, not the long-lived Flask process which
        legitimately reserves >1 GiB at idle.

The validator (CadQueryValidator) still runs in-process; we only spawn a
subprocess once the script is known to be syntactically and statically OK.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import Flask, request, send_file

from CadQueryValidator import CadQueryValidator

try:
    from dotenv import load_dotenv

    repo_root_env = Path(__file__).resolve().parent.parent / ".env"
    if repo_root_env.is_file():
        load_dotenv(repo_root_env)
except ImportError:
    pass


app = Flask(__name__)
validator = CadQueryValidator()

# --- Paths / config ----------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_WORKER = _HERE / "worker.py"
_PYTHON = sys.executable

EXEC_TIMEOUT_SEC = float(os.environ.get("CADQUERY_EXEC_TIMEOUT", "30"))


# --- Subprocess driver -------------------------------------------------------


def _run_worker(code: str, mode: str, output_suffix: str):
    """
    @brief Validate + run a user script in an isolated subprocess.
    @param code Raw user code.
    @param mode One of 'preview', 'stl', 'step'.
    @param output_suffix Extension for the worker's output file
                         ('.json', '.stl', '.step').
    @return (output_path, error)
            output_path: path to the worker's output file (caller is
                         responsible for cleanup) or None on error.
            error:       error message string or None on success.

    Lifecycle:
        1. Run the static validator. Reject (None, message) on failure.
        2. Write the cleaned code to a temp file.
        3. Spawn `python worker.py <code> <output> <mode>` with timeout.
        4. On timeout: kill, cleanup, return ("Execution timeout exceeded …").
        5. On non-zero exit: return (None, stderr-summary).
        6. On success: return (output_path, None).
    """
    cleaned_code, error = validator.validate(code)
    if error:
        return None, error

    code_fd, code_path = tempfile.mkstemp(suffix=".py", prefix="cqcode_")
    out_fd, output_path = tempfile.mkstemp(suffix=output_suffix, prefix="cqout_")
    os.close(out_fd)
    try:
        with os.fdopen(code_fd, "w", encoding="utf-8") as fh:
            fh.write(cleaned_code)
    except Exception:
        try:
            os.unlink(code_path)
        except OSError:
            pass
        try:
            os.unlink(output_path)
        except OSError:
            pass
        raise

    proc = None
    try:
        proc = subprocess.Popen(
            [_PYTHON, str(_WORKER), code_path, output_path, mode],
            cwd=str(_HERE),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=EXEC_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            return None, (
                f"Execution timeout exceeded ({EXEC_TIMEOUT_SEC:g}s). "
                "Possible infinite loop or runaway computation."
            )

        if proc.returncode != 0:
            err = (stderr or "").strip()
            if not err:
                signal_hint = ""
                if proc.returncode is not None and proc.returncode < 0:
                    signal_hint = f" (signal {-proc.returncode})"
                err = f"Worker exited with code {proc.returncode}{signal_hint}"
            return None, err

        return output_path, None
    finally:
        try:
            os.unlink(code_path)
        except OSError:
            pass
        # NOTE: output_path cleanup is the caller's job on success;
        # on failure we wipe it here to avoid leaking temp files.
        if proc is None or proc.returncode != 0:
            try:
                os.unlink(output_path)
            except OSError:
                pass


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

    Returns 200 'ok' as long as the Flask app is reachable. We deliberately
    do NOT spawn a worker here so that the watchdog can ping cheaply every
    30 s without warming up a CadQuery import.
    """
    return ("ok", 200, {"Content-Type": "text/plain"})


@app.route("/preview", methods=["POST"])
def run_preview():
    output_path = None
    try:
        code = request.json["code"]
        output_path, error = _run_worker(code, "preview", ".json")
        if error:
            return make_response(message=error, status=400)
        with open(output_path, "r", encoding="utf-8") as fh:
            mesh_data = json.load(fh)
        return make_response(data=mesh_data, message="Preview generated successfully")
    except Exception as e:
        return make_response(message=str(e), status=500)
    finally:
        if output_path:
            try:
                os.unlink(output_path)
            except OSError:
                pass


def _send_export(code: str, mode: str, suffix: str, download_name: str):
    """Common /stl + /step handler: run worker, stream the file from memory.

    Why memory rather than `send_file(path)` + call_on_close: Flask's dev
    server (Werkzeug) does not always fire call_on_close (e.g. when the
    client disconnects, or with some forwarding setups), leaking temp
    files in /tmp. STL/STEP outputs for our use case are small (<10 MB),
    so we slurp them and unlink immediately.
    """
    output_path, error = _run_worker(code, mode, suffix)
    if error:
        return make_response(message=error, status=400)
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
        return _send_export(code, "stl", ".stl", "model.stl")
    except Exception as e:
        return make_response(message=str(e), status=500)


@app.route("/step", methods=["POST"])
def run_step():
    try:
        code = request.json["code"]
        return _send_export(code, "step", ".step", "model.step")
    except Exception as e:
        return make_response(message=str(e), status=500)


if __name__ == "__main__":
    host = os.environ.get("CADQUERY_HOST", "127.0.0.1")
    port = int(os.environ.get("CADQUERY_PORT", "5002"))
    print(f"CadQuery server starting on http://{host}:{port}")
    print(
        f"[server] subprocess sandbox: exec_timeout={EXEC_TIMEOUT_SEC:g}s, "
        f"worker={_WORKER.name}"
    )
    app.run(host=host, port=port)
