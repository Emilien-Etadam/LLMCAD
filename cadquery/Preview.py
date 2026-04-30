"""
@file Preview.py
@brief Generate preview mesh data from Build123d objects.

Phase 5: rewritten on top of build123d. The previous implementation
relied on cadquery's `Workplane.objects` and `Assembly.toCompound()`,
both of which no longer exist. The accepted result types are now Part,
Compound, or Solid -- the same set the worker accepts.

This module is kept around for symmetry / external callers; the in-tree
worker (worker.py) does its own preview generation to avoid an extra
import path. They share the same tessellation parameters and output
schema.
"""

from typing import Any, Dict, List

import build123d as _b3d


def _extract_solids(result: Any) -> List[_b3d.Solid]:
    """Normalize a build123d result into a flat list of Solids.

    Order matters: Sketch / Curve inherit from Compound, so 2D guards
    must run before the Compound branch. Returns an empty list for
    unsupported types so the caller can build a useful error message.
    """
    if isinstance(result, (_b3d.Sketch, _b3d.Curve)):
        return []
    if isinstance(result, (_b3d.Part, _b3d.Compound)):
        return list(result.solids())
    if isinstance(result, _b3d.Solid):
        return [result]
    return []


def compute_preview(result: Any) -> Dict[str, Any]:
    """Tessellate `result` and return the JSON-ready preview payload.

    Output schema (consumed by the three.js frontend):
      vertices : flat list [x1,y1,z1, x2,y2,z2, ...]
      faces    : list of [i, j, k] triangle index triples
      objectCount: number of solids tessellated

    Tessellation parameters (0.1, 0.1) match the worker's `_do_preview`
    so /preview and /stl are visually consistent on the frontend.
    """
    solids = _extract_solids(result)
    if not solids:
        raise RuntimeError(
            f"aucun solide trouvé (type reçu: {type(result).__name__})"
        )

    all_vertices: List[float] = []
    all_faces: List[List[int]] = []
    vertex_offset = 0
    for solid in solids:
        verts, tris = solid.tessellate(tolerance=0.1, angular_tolerance=0.1)
        for v in verts:
            all_vertices.extend([v.X, v.Y, v.Z])
        for tri in tris:
            all_faces.append([
                tri[0] + vertex_offset,
                tri[1] + vertex_offset,
                tri[2] + vertex_offset,
            ])
        vertex_offset += len(verts)

    return {
        "vertices": all_vertices,
        "faces": all_faces,
        "objectCount": len(solids),
    }
