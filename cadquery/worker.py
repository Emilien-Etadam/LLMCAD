"""
@file worker.py
@brief Subprocess worker that executes a single user Build123d script.

@usage
    python worker.py <code_file> <output_file> <mode>

      <code_file>   Path to the user script.
      <output_file> Where to write the result:
                      mode=preview -> JSON ({vertices, faces, objectCount})
                      mode=stl     -> binary STL
                      mode=step    -> ASCII STEP
      <mode>        One of: preview | stl | step.

@exit codes
    0 - success, output file is valid.
    1 - runtime error (Build123d / OCC failure, missing `result`, etc.).
    2 - validation / argument error (bad mode, unreadable code file, ...).

@notes
    Phase 5 (Build123d migration):
      - The previous AST-level validator (CadQueryValidator) is gone. The
        only sandboxing left is process isolation + RLIMIT_AS. Local-only
        deployment is assumed; do NOT expose this server to the public
        internet without re-introducing a validator.
      - The user script is `exec()`d in a fresh namespace prepopulated with
        `from build123d import *`. The script must assign its 3D result to
        a variable named `result`.
      - Segfaults / runaway memory in OpenCascade tear down only this
        worker; the parent Flask server stays alive and the next request
        spawns a fresh subprocess.
"""

import json
import os
import resource
import sys
import traceback
from typing import Any, List

# RLIMIT_AS must be set BEFORE importing the heavy stack (build123d / OCP).
# build123d 0.10 + cadquery-ocp 7.9 + numpy reserve roughly 1.3 GiB of
# virtual address space at idle, so a too-tight cap segfaults the worker
# at import time. Default to 2 GiB (idle ~1.3 GiB + headroom for the user
# script). Operators can tighten / loosen via env var:
#     CADQUERY_WORKER_MEM_LIMIT_MB=512   # tighter (will likely crash)
#     CADQUERY_WORKER_MEM_LIMIT_MB=0     # disabled
_MEM_LIMIT_MB = int(os.environ.get("CADQUERY_WORKER_MEM_LIMIT_MB", "2048"))


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


import build123d as _b3d  # noqa: E402 - intentionally below RLIMIT_AS application

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_VALIDATION = 2


def _extract_solids(result: Any) -> List[_b3d.Solid]:
    """Normalize a user `result` into a flat list of Solids.

    Order of isinstance checks matters: Sketch / Curve / Part all inherit
    from Compound, so the 2D guards must run first.
    """
    if isinstance(result, (_b3d.Sketch, _b3d.Curve)):
        raise RuntimeError(
            "le résultat doit être 3D, pas une esquisse/courbe"
        )
    if isinstance(result, (_b3d.Part, _b3d.Compound)):
        return list(result.solids())
    if isinstance(result, _b3d.Solid):
        return [result]
    raise RuntimeError(f"type non supporté: {type(result).__name__}")


def _to_compound(result: Any) -> _b3d.Compound:
    """Wrap any supported result into a Compound for STEP/STL export."""
    if isinstance(result, _b3d.Compound):
        return result
    if isinstance(result, _b3d.Solid):
        return _b3d.Compound([result])
    raise RuntimeError(f"type non supporté pour export: {type(result).__name__}")


def _do_preview(result: Any, output_path: str) -> int:
    solids = _extract_solids(result)
    if not solids:
        raise RuntimeError("aucun solide trouvé dans le résultat")
    all_vertices: List[float] = []
    all_faces: List[List[int]] = []
    vertex_offset = 0
    for solid in solids:
        verts, tris = solid.tessellate(tolerance=0.1, angular_tolerance=0.1)
        for v in verts:
            all_vertices.extend([v.X, v.Y, v.Z])
        for tri in tris:
            all_faces.append([tri[0] + vertex_offset, tri[1] + vertex_offset, tri[2] + vertex_offset])
        vertex_offset += len(verts)
    payload = {
        "vertices": all_vertices,
        "faces": all_faces,
        "objectCount": len(solids),
    }
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return EXIT_OK


def _do_stl(result: Any, output_path: str) -> int:
    compound = _to_compound(result)
    _b3d.export_stl(compound, output_path, tolerance=0.01, angular_tolerance=0.1)
    return EXIT_OK


def _do_step(result: Any, output_path: str) -> int:
    compound = _to_compound(result)
    _b3d.export_step(compound, output_path)
    return EXIT_OK


def main() -> int:
    if len(sys.argv) != 4:
        sys.stderr.write("Usage: worker.py <code_file> <output_file> <mode>\n")
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

    # Prepopulate the namespace with `from build123d import *` so user code
    # need not (and typically does not) repeat the import. We also expose
    # `b3d` for explicit access if desired.
    namespace: dict = {"__name__": "__user_script__", "b3d": _b3d}
    exec("from build123d import *", namespace)

    try:
        exec(code, namespace)
    except BaseException as exc:  # noqa: BLE001 - surface any user-code failure
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        traceback.print_exc(file=sys.stderr)
        return EXIT_RUNTIME

    if "result" not in namespace:
        sys.stderr.write("Code did not assign a value to 'result'\n")
        return EXIT_RUNTIME
    result = namespace["result"]

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
