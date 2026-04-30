"""
@file Preview.py
@brief Generate preview mesh data from CadQuery objects.
@author 30hours

Phase 4.5: accept any of `cq.Workplane`, `cq.Assembly`, `cq.Compound`, or
`cq.occ_impl.shapes.Solid` as a result. The caller (server.py / worker.py)
no longer constrains the script to assign a Workplane to `result`.
"""

import cadquery as cq


def extract_solids(shape):
  """
  @brief Extract all solids from a single OCC/CadQuery shape.

  Handles bare Solids and Compounds (which is what `cq.Assembly.toCompound()`
  returns and what often shows up as the first/only entry of `Workplane.objects`).
  """
  if isinstance(shape, cq.occ_impl.shapes.Solid):
    return [shape]
  if isinstance(shape, cq.occ_impl.shapes.Compound):
    return list(shape.Solids())
  return []


def _result_solids(result):
  """
  @brief Normalize any supported result type into a flat list of Solids.

  Supported inputs:
    - cq.Workplane  -> iterate `.objects`, extracting solids/compounds.
    - cq.Assembly   -> convert to compound, list its Solids().
    - cq.Compound   -> list its Solids() directly.
    - cq.Solid      -> single-element list.
  Anything else returns an empty list (caller surfaces a useful error).
  """
  if isinstance(result, cq.Workplane):
    out = []
    for obj in result.objects:
      out.extend(extract_solids(obj))
    return out
  if isinstance(result, cq.Assembly):
    return list(result.toCompound().Solids())
  if isinstance(result, cq.occ_impl.shapes.Compound):
    return list(result.Solids())
  if isinstance(result, cq.occ_impl.shapes.Solid):
    return [result]
  return []


def preview(result):
  """
  @brief Generate preview mesh data from a CadQuery result.
  @param result: cq.Workplane | cq.Assembly | cq.Compound | cq.Solid
  @return (dict, None) on success; (None, error_message) on failure.

  Output dict structure (three.js consumer):
    'vertices': [x1,y1,z1, x2,y2,z2, ...],
    'faces':    [v1,v2,v3, v4,v5,v6, ...],
    'objectCount': number of solids tessellated.

  Coarse tessellation parameters (1.0, 1.0) match cq-editor's instant preview.
  """
  solids = _result_solids(result)
  if not solids:
    return None, "No solids found in result"

  all_vertices = []
  all_faces = []
  vertex_offset = 0
  for solid in solids:
    mesh = solid.tessellate(1.0, 1.0)
    for vertex in mesh[0]:
      all_vertices.extend([vertex.x, vertex.y, vertex.z])
    for face in mesh[1]:
      all_faces.extend([v + vertex_offset for v in face])
    vertex_offset += len(mesh[0])

  return {
    'vertices': all_vertices,
    'faces': all_faces,
    'objectCount': len(solids),
  }, None
