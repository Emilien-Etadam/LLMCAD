"""
@file worker.py
@brief Subprocess worker that executes a single validated CadQuery script.

@usage
    python worker.py <code_file> <output_file> <mode>

      <code_file>   Path to the user script (already validated upstream).
      <output_file> Where to write the result:
                      mode=preview -> JSON (vertices/faces/objectCount)
                      mode=stl     -> binary STL
                      mode=step    -> ASCII STEP
      <mode>        One of: preview | stl | step.

@exit codes
    0 - success, output file is valid.
    1 - runtime error (CadQuery / OCC failure, missing `result`, etc.).
    2 - validation / argument error (bad mode, unreadable code file, ...).

@notes
    - Runs in its own process; segfaults in OpenCascade or runaway memory
      tear down only this process and leave the parent Flask server alive.
    - `RLIMIT_AS` is set to 1 GiB before doing any heavy import. CadQuery +
      cadquery-ocp + numpy idle around 450 MB, so 1 GiB leaves ~550 MB for
      the user script before MemoryError.
    - Defense in depth: the same restricted-builtins / sandboxed-import shim
      used by the (now-deprecated) in-process executor is rebuilt here from
      `CadQueryValidator.allowed_builtins`. The worker never imports user
      code; it `exec()`s it with these globals.
"""

import builtins as _builtins
import json
import math
import os
import resource
import sys
import traceback

# RLIMIT_AS must be set BEFORE importing the heavy stack (cadquery / OCP).
# The phase 4.5 spec calls for a 1 GiB cap, but on this stack
# (cadquery 2.7 + cadquery-ocp 7.8.1.1 + numpy 2.4.4) `import cadquery`
# alone reserves ~1.3 GiB of virtual address space, so a 1 GiB cap segfaults
# the worker at import time. We default to 2 GiB (idle ~1.3 GiB + headroom
# for the user script). Operators can tighten / loosen via env var:
#     CADQUERY_WORKER_MEM_LIMIT_MB=512   # tighter (will likely crash)
#     CADQUERY_WORKER_MEM_LIMIT_MB=0     # disabled
_MEM_LIMIT_MB = int(os.environ.get("CADQUERY_WORKER_MEM_LIMIT_MB", "2048"))


def _apply_memory_limit():
    """Best-effort RLIMIT_AS cap. Failure is logged but non-fatal.

    Called BEFORE the heavy imports below so the cap actually applies to the
    cadquery / numpy mmap'd regions.
    """
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


import cadquery as cq  # noqa: E402 - intentionally below RLIMIT_AS application
import numpy as np  # noqa: E402

from CadQueryValidator import CadQueryValidator  # noqa: E402

# --- Exit codes -------------------------------------------------------------

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_VALIDATION = 2

# Modules importable from inside the sandbox at runtime. Mirrors
# CadQueryValidator's import whitelist.
_RUNTIME_ALLOWED_IMPORTS = {"cadquery", "math", "numpy", "typing"}


def _sandbox_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Restricted __import__ injected into the sandbox builtins."""
    if level != 0:
        raise ImportError("Relative imports are not allowed in sandbox")
    root = name.split(".")[0]
    if root not in _RUNTIME_ALLOWED_IMPORTS:
        raise ImportError(f"Import of '{name}' is not allowed in sandbox")
    return __import__(name, globals, locals, fromlist, level)


def _build_safe_builtins(validator):
    safe = {
        name: getattr(_builtins, name)
        for name in validator.allowed_builtins
        if hasattr(_builtins, name)
    }
    safe["__import__"] = _sandbox_import
    return safe


# --- Result handling --------------------------------------------------------


def _extract_solids(shape):
    """Pull Solids out of a single CadQuery shape (Solid or Compound)."""
    if isinstance(shape, cq.occ_impl.shapes.Solid):
        return [shape]
    if isinstance(shape, cq.occ_impl.shapes.Compound):
        return list(shape.Solids())
    return []


def _result_solids(result):
    """Normalize any supported result into a list of Solids."""
    if isinstance(result, cq.Workplane):
        out = []
        for obj in result.objects:
            out.extend(_extract_solids(obj))
        return out
    if isinstance(result, cq.Assembly):
        return list(result.toCompound().Solids())
    if isinstance(result, cq.occ_impl.shapes.Compound):
        return list(result.Solids())
    if isinstance(result, cq.occ_impl.shapes.Solid):
        return [result]
    return []


def _to_exportable(result):
    """Coerce result to something `cq.exporters.export` accepts.

    cq.exporters.export handles Workplane and Shape (incl. Compound/Solid),
    but NOT Assembly. For Assembly we collapse to its Compound; this loses
    the named-part hierarchy in STEP, but produces a valid file. (For full
    hierarchy preservation, a future revision could call Assembly.save()
    directly when the user assigns an Assembly to `result`.)
    """
    if isinstance(result, cq.Assembly):
        return result.toCompound()
    return result


def _do_preview(result, output_path):
    solids = _result_solids(result)
    if not solids:
        sys.stderr.write("No solids found in result\n")
        return EXIT_RUNTIME
    all_vertices = []
    all_faces = []
    vertex_offset = 0
    for solid in solids:
        mesh = solid.tessellate(1.0, 1.0)
        for vertex in mesh[0]:
            all_vertices.extend([vertex.x, vertex.y, vertex.z])
        for face in mesh[1]:
            all_faces.extend([i + vertex_offset for i in face])
        vertex_offset += len(mesh[0])
    payload = {
        "vertices": all_vertices,
        "faces": all_faces,
        "objectCount": len(solids),
    }
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return EXIT_OK


def _do_stl(result, output_path):
    cq.exporters.export(_to_exportable(result), output_path, exportType="STL")
    return EXIT_OK


def _do_step(result, output_path):
    cq.exporters.export(_to_exportable(result), output_path, exportType="STEP")
    return EXIT_OK


# --- Entry point ------------------------------------------------------------


def main():
    if len(sys.argv) != 4:
        sys.stderr.write("Usage: worker.py <code_file> <output_file> <mode>\n")
        return EXIT_VALIDATION

    code_path = sys.argv[1]
    output_path = sys.argv[2]
    mode = sys.argv[3]

    if mode not in {"preview", "stl", "step"}:
        sys.stderr.write(f"Invalid mode '{mode}'\n")
        return EXIT_VALIDATION

    # RLIMIT_AS was already applied at module import time, before cadquery.

    try:
        with open(code_path, "r", encoding="utf-8") as fh:
            code = fh.read()
    except OSError as exc:
        sys.stderr.write(f"Could not read code file '{code_path}': {exc}\n")
        return EXIT_VALIDATION

    validator = CadQueryValidator()
    safe_builtins = _build_safe_builtins(validator)
    globals_dict = {
        "cq": cq,
        "np": np,
        "math": math,
        "__builtins__": safe_builtins,
    }
    locals_dict = {}

    try:
        exec(code, globals_dict, locals_dict)
    except BaseException as exc:  # noqa: BLE001 - surface any user-code failure
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        traceback.print_exc(file=sys.stderr)
        return EXIT_RUNTIME

    if "result" not in locals_dict:
        sys.stderr.write("Code did not assign a value to 'result'\n")
        return EXIT_RUNTIME
    result = locals_dict["result"]

    try:
        if mode == "preview":
            return _do_preview(result, output_path)
        if mode == "stl":
            return _do_stl(result, output_path)
        if mode == "step":
            return _do_step(result, output_path)
    except BaseException as exc:  # noqa: BLE001 - export/tessellate failures
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        traceback.print_exc(file=sys.stderr)
        return EXIT_RUNTIME

    return EXIT_RUNTIME


if __name__ == "__main__":
    sys.exit(main())
